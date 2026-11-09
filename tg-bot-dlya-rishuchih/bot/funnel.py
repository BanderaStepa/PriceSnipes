"""Бізнес-логіка воронки «Для рішучих»: старт, пакети, опис, оплата, відповіді на текст.

Обробники aiogram (handlers.py) і фоновий планувальник (scheduler.py) працюють через цей клас.
Стан зберігається лише в БД; у пам'яті — тільки кеш останнього показаного значення живого таймера.
"""

from __future__ import annotations

import dataclasses
import logging

from aiogram.types import InlineKeyboardMarkup

from .content import Content, Package, Reminder
from .sender import Outcome, Result, Sender
from .storage import (
    EVENT_PACKAGE,
    EVENT_PACKAGES,
    EVENT_PAYMENT_LINK,
    EVENT_RESTART,
    EVENT_START,
    Profile,
    Storage,
    UserRecord,
)
from .texts import Texts
from .timeutils import Clock

log = logging.getLogger(__name__)

# Повідомлення «пропозиція завершилась» надсилаємо не пізніше ніж через 2 год після дедлайну,
# щоб після довгого простою сервера не писати людям застарілих повідомлень.
EXPIRED_NOTICE_GRACE = 2 * 3600


class Funnel:
    def __init__(
        self,
        *,
        storage: Storage,
        content: Content,
        sender: Sender,
        clock: Clock,
        manager_chat_id: int | None = None,
    ) -> None:
        self.storage = storage
        self.content = content
        self.texts = Texts(content)
        self.sender = sender
        self.clock = clock
        self.manager_chat_id = manager_chat_id
        # (user_id, message_id) → (останній показаний текст оферу, коли його показали)
        self.timer_cache: dict[tuple[int, int], tuple[str, int]] = {}

    # ------------------------------------------------------------------ дії користувача

    async def start(self, profile: Profile, source: str | None = None) -> None:
        """/start: перший раз — запуск таймера; повторно — той самий таймер, офер надсилається заново."""
        now = self.clock.now()
        user, created = await self.storage.register_start(
            profile, now=now, duration=self.content.settings.offer_duration, source=source
        )
        await self.storage.add_event(user.user_id, EVENT_START if created else EVENT_RESTART, now=now)
        if created:
            log.info("Новий користувач %s (%s): пропозиція діє до %s",
                     user.user_id, source or "без джерела", self.texts.deadline(user))

        if user.is_expired(now):
            await self._send_expired(user, now)
            return

        body_id: int | None = None
        if self.texts.separate_timer:
            # Текст оферу окремо: його ніколи не редагуємо (шторки й спойлери не згортаються),
            # а таймер і кнопка — короткими повідомленням під ним.
            body = await self._send(user, self.texts.offer(user, now))
            if not body.ok or body.message_id is None:
                return
            body_id = body.message_id
            text = self.texts.offer_timer(user, now)
        else:
            text = self.texts.offer(user, now)
        result = await self._send(user, text, self.texts.keyboard("offer", user, now))
        if not result.ok or result.message_id is None:
            return
        await self.storage.set_offer_message(user.user_id, result.message_id, body_id)
        self.timer_cache[(user.user_id, result.message_id)] = (text, now)
        # У чаті лишається один офер із живим таймером — попередній (обидва його повідомлення) прибираємо.
        for old in (user.offer_message_id, user.offer_body_message_id):
            if old and old not in (result.message_id, body_id):
                self.timer_cache.pop((user.user_id, old), None)
                await self.sender.delete(user.chat_id, old)

    async def show_packages(self, profile: Profile) -> None:
        """«Обрати пакет» / «Подивитися інші пакети»."""
        user = await self._known_user(profile)
        if user is None:
            return
        now = self.clock.now()
        if not self.texts.packages_open(user, now):
            await self._send_expired(user, now)
            return
        await self.storage.add_event(user.user_id, EVENT_PACKAGES, now=now)
        await self._send(user, self.texts.packages_list(user, now), self.texts.packages_keyboard(user, now))

    async def show_package(self, profile: Profile, package_id: str) -> None:
        """Розширений опис пакета з кнопками «Отримати посилання на оплату» / «Подивитися інші пакети»."""
        user = await self._known_user(profile)
        if user is None:
            return
        now = self.clock.now()
        if not self.texts.packages_open(user, now):
            await self._send_expired(user, now)
            return
        package = self.content.package(package_id)
        if package is None:  # пакет прибрали з content.yaml, а кнопка лишилась у старому повідомленні
            await self._send(user, self.texts.packages_list(user, now), self.texts.packages_keyboard(user, now))
            return
        await self.storage.add_event(user.user_id, EVENT_PACKAGE, now=now, package_id=package.id)
        await self._send(
            user, self.texts.package_description(package, user, now), self.texts.keyboard("package", user, now, package)
        )

    async def send_payment_link(self, profile: Profile, package_id: str) -> None:
        """«Отримати посилання на оплату»: посилання відповідного пакета. Оплата не перевіряється."""
        user = await self._known_user(profile)
        if user is None:
            return
        now = self.clock.now()
        if not self.texts.packages_open(user, now):
            await self._send_expired(user, now)
            return
        package = self.content.package(package_id)
        if package is None:
            await self._send(user, self.texts.packages_list(user, now), self.texts.packages_keyboard(user, now))
            return
        if not package.url_for(self.texts.is_expired(user, now)):  # оплату для пакета ще не налаштовано
            await self._send(
                user, self.texts.package_description(package, user, now), self.texts.keyboard("package", user, now, package)
            )
            return
        await self.storage.add_event(user.user_id, EVENT_PAYMENT_LINK, now=now, package_id=package.id)
        log.info("Користувач %s отримав посилання на оплату пакета %s", user.user_id, package.id)
        await self._send(user, self.texts.payment(package, user, now), self.texts.keyboard("payment", user, now, package))
        await self._notify_manager(package, user, now)

    async def on_text(self, profile: Profile) -> None:
        """Будь-яке інше повідомлення від користувача."""
        user = await self._known_user(profile)
        if user is None:
            return
        now = self.clock.now()
        if not self.texts.packages_open(user, now):
            await self._send_expired(user, now)
            return
        text = self.texts.fallback(user, now)
        if text:
            await self._send(user, text, self.texts.keyboard("fallback", user, now))

    def is_known_package(self, package_id: str) -> bool:
        return self.content.package(package_id) is not None

    # ------------------------------------------------------------------ фонові дії (планувальник)

    def is_silent_time(self, now: int) -> bool:
        """Тихі години: повідомлення приходять без звуку."""
        silent = self.content.settings.silent_hours
        return silent is not None and silent.contains(now, self.content.settings.timezone)

    async def send_reminder(self, user: UserRecord, reminder: Reminder, now: int) -> Outcome:
        result = await self.sender.send(
            user.chat_id,
            self.texts.reminder(reminder, user, now),
            self.texts.keyboard("reminder", user, now),
            silent=self.is_silent_time(now),
            background=True,
        )
        if result.ok:
            log.info("Нагадування %s надіслано користувачу %s", reminder.id, user.user_id)
        return result.outcome

    async def refresh_timer(self, user: UserRecord, now: int) -> Outcome | None:
        """Оновлює живий таймер в офері, якщо показане значення змінилось. None — оновлювати не треба."""
        every = self.content.settings.timer_update_every
        if not every or user.offer_message_id is None:
            return None
        key = (user.user_id, user.offer_message_id)
        text = self.texts.live_offer(user, now, has_body=user.offer_body_message_id is not None)
        cached = self.timer_cache.get(key)
        if cached is not None and (cached[0] == text or now - cached[1] < every):
            return None
        result = await self.sender.edit(
            user.chat_id, user.offer_message_id, text, self.texts.keyboard("offer", user, now), background=True
        )
        if result.ok:
            self.timer_cache[key] = (text, now)
        elif result.outcome is Outcome.GONE:  # людина видалила повідомлення — більше його не чіпаємо
            await self.storage.clear_offer_message(user.user_id, user.offer_message_id)
            self.timer_cache.pop(key, None)
        return result.outcome

    async def finish_offer(self, user: UserRecord, now: int) -> Outcome:
        """Дедлайн минув: таймер в офері → «час вийшов», кнопки прибираються (або лишаються, якщо продаж триває);
        за налаштуванням — окреме повідомлення. RETRY — тимчасова проблема, повторити на наступному проході."""
        blocked = user.is_blocked
        if user.offer_message_id is not None and not blocked:
            result = await self.sender.edit(
                user.chat_id,
                user.offer_message_id,
                self.texts.live_offer(user, now, has_body=user.offer_body_message_id is not None),
                self.texts.keyboard("offer", user, now),
                background=True,
            )
            if result.outcome is Outcome.RETRY:
                return Outcome.RETRY
            blocked = result.outcome is Outcome.BLOCKED
            self.timer_cache.pop((user.user_id, user.offer_message_id), None)

        settings = self.content.settings
        if settings.send_expired_notice and not blocked and now - user.deadline_at <= EXPIRED_NOTICE_GRACE:
            result = await self.sender.send(
                user.chat_id,
                self.texts.expired_notice(user, now),
                self.texts.keyboard("expired", user, now),
                silent=self.is_silent_time(now),
                background=True,
            )
            if result.outcome is Outcome.RETRY:
                return Outcome.RETRY
        await self.storage.mark_expiry_done(user.user_id)
        log.info("Пропозиція для користувача %s завершилась", user.user_id)
        return Outcome.OK

    # ------------------------------------------------------------------ допоміжне

    async def _known_user(self, profile: Profile) -> UserRecord | None:
        """Користувач із БД з оновленим профілем. Якщо його немає (кнопка без /start) — звичайний старт."""
        user = await self.storage.get_user(profile.user_id)
        if user is None:
            await self.start(profile)
            return None
        now = self.clock.now()
        await self.storage.touch(profile, now=now)
        return dataclasses.replace(
            user,
            chat_id=profile.chat_id,
            first_name=profile.first_name,
            last_name=profile.last_name,
            username=profile.username,
            language_code=profile.language_code,
            blocked_at=None,
            last_seen_at=now,
        )

    async def _send(self, user: UserRecord, text: str, keyboard: InlineKeyboardMarkup | None = None) -> Result:
        return await self.sender.send(user.chat_id, text, keyboard)

    async def _send_expired(self, user: UserRecord, now: int) -> Result:
        return await self._send(user, self.texts.expired(user, now), self.texts.keyboard("expired", user, now))

    async def _notify_manager(self, package: Package, user: UserRecord, now: int) -> None:
        if self.manager_chat_id is None:
            return
        await self.sender.send(
            self.manager_chat_id, self.texts.manager_payment_request(package, user, now), user_chat=False
        )
