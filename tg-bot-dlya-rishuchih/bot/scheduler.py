"""Фоновий планувальник: нагадування, завершення пропозиції, живий таймер.

Кожен прохід читає стан із БД, тому перезапуск сервера нічого не ламає: після старту планувальник просто
продовжує з того місця, де все зупинилось (пропущене за час простою — див. process_reminders).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

from .funnel import Funnel
from .sender import Outcome
from .storage import REMINDER_FAILED, REMINDER_SENT, REMINDER_SKIPPED

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, funnel: Funnel, *, tick_seconds: float = 15.0) -> None:
        self.funnel = funnel
        self.tick_seconds = tick_seconds
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def storage(self):  # noqa: ANN201 — для стислості
        return self.funnel.storage

    def _now(self) -> int:
        return self.funnel.clock.now()

    # ------------------------------------------------------------------ один прохід

    async def process_reminders(self) -> int:
        """Надсилає нагадування, час яких настав. Повертає кількість надісланих.

        Якщо для людини настав час кількох нагадувань одразу (сервер не працював), надсилається лише
        найсвіжіше, решта позначаються пропущеними — щоб не прийшла пачка повідомлень.
        """
        reminders = self.funnel.content.reminders
        if not reminders:
            return 0
        now = self._now()
        users = await self.storage.active_users(now)
        if not users:
            return 0
        handled = await self.storage.reminder_log(now)
        sent = 0
        for user in users:
            done = handled.get(user.user_id, set())
            due = [
                r for r in reminders
                if r.id not in done and r.due_at(user.started_at, user.deadline_at) <= now < user.deadline_at
            ]
            if not due:
                continue
            latest = max(due, key=lambda r: r.due_at(user.started_at, user.deadline_at))
            for reminder in due:
                if reminder is not latest:
                    await self.storage.log_reminder(user.user_id, reminder.id, REMINDER_SKIPPED, now=now)
            outcome = await self.funnel.send_reminder(user, latest, self._now())
            if outcome is Outcome.OK:
                await self.storage.log_reminder(user.user_id, latest.id, REMINDER_SENT, now=now)
                sent += 1
            elif outcome is not Outcome.RETRY:  # RETRY — спробуємо на наступному проході
                await self.storage.log_reminder(user.user_id, latest.id, REMINDER_FAILED, now=now)
        return sent

    async def process_expiry(self) -> int:
        """Завершує пропозиції, у яких минув дедлайн. Повертає кількість завершених."""
        finished = 0
        for user in await self.storage.pending_expiry(self._now()):
            if await self.funnel.finish_offer(user, self._now()) is Outcome.OK:
                finished += 1
        return finished

    async def update_timers(self) -> int:
        """Оновлює живі таймери в оферах. Повертає кількість відредагованих повідомлень."""
        if not self.funnel.content.settings.timer_update_every:
            return 0
        edited = 0
        live: set[tuple[int, int]] = set()
        for user in await self.storage.live_offers(self._now()):
            live.add((user.user_id, user.offer_message_id))  # type: ignore[arg-type]
            if await self.funnel.refresh_timer(user, self._now()) is Outcome.OK:
                edited += 1
        cache = self.funnel.timer_cache
        for key in [k for k in cache if k not in live]:
            del cache[key]
        pause, now = self.funnel.timer_pause, self._now()
        for chat_id in [c for c, until in pause.items() if until <= now]:
            del pause[chat_id]
        return edited

    @property
    def timer_tick_seconds(self) -> float:
        """Як часто перевіряти живі таймери: зі швидким відліком — щосекунди (щоб крок 5 с був рівним)."""
        if self.funnel.content.settings.timer_fast_every:
            return min(self.tick_seconds, 1.0)
        return self.tick_seconds

    async def tick(self) -> None:
        """Повний прохід (для тестів і ручного запуску)."""
        await self.process_reminders()
        await self.process_expiry()
        await self.update_timers()

    # ------------------------------------------------------------------ фонові цикли

    def start(self) -> None:
        if self._tasks:
            return
        self._tasks = [
            asyncio.create_task(self._loop("нагадування", self._reminders_and_expiry, self.tick_seconds),
                                name="reminders"),
            asyncio.create_task(self._loop("живий таймер", self.update_timers, self.timer_tick_seconds), name="timers"),
        ]
        log.info("Планувальник запущено (крок %s с, живий таймер — %s с)", self.tick_seconds, self.timer_tick_seconds)

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _reminders_and_expiry(self) -> None:
        await self.process_reminders()
        await self.process_expiry()

    async def _loop(self, name: str, job: Callable[[], Awaitable[object]], period: float) -> None:
        while True:
            started = time.monotonic()
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception:  # помилка одного проходу не зупиняє бота
                log.exception("Планувальник (%s): помилка під час проходу — продовжую роботу", name)
            await asyncio.sleep(max(0.01, period - (time.monotonic() - started)))
