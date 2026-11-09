"""Бізнес-логіка воронки «Для рішучих»: старт, пакети, опис, оплата, відповіді на текст.

Обробники aiogram (handlers.py) і фоновий планувальник (scheduler.py) працюють через цей клас.
Стан зберігається лише в БД; у пам'яті — тільки кеш останнього показаного значення живого таймера
(і паузи, про які попросив Telegram).
"""

from __future__ import annotations

import dataclasses
import logging

from aiogram.types import FSInputFile, InlineKeyboardMarkup

from .content import PHOTO_CAPTION_LIMIT, Content, Package, Reminder
from .htmltext import strip_tags
from .paths import project_path
from .sender import Outcome, Result, Sender
from .storage import (
    EVENT_COURSE_INFO,
    EVENT_GIFT,
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


@dataclasses.dataclass(frozen=True)
class _PreviewPerson:
    """Уявний користувач для /preview: пропозиція щойно почалась."""

    user_id: int
    first_name: str
    username: str | None
    deadline_at: int

def timer_step(interval: int) -> int:
    """Крок відліку {countdown} для інтервалу оновлення: 5 с → значення кратні 5 с; хвилина і довше → цілі хвилини."""
    return max(1, min(int(interval), 60))


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
        # чат → до якого моменту не редагувати таймер (Telegram попросив зачекати — flood control)
        self.timer_pause: dict[int, int] = {}
        # картинка пакета → file_id у Telegram: файл завантажується один раз, далі — миттєво за id
        self.photo_ids: dict[str, str] = {}

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
            text = self.texts.offer_timer(user, now, timer_step(self.timer_interval(user, now)))
        else:
            text = self.texts.offer(user, now, timer_step(self.timer_interval(user, now)))
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
        await self._send_package(user, package, now)

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
            await self._send_package(user, package, now)
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

    async def show_gift(self, profile: Profile) -> None:
        """«🎁 Що за подарунок?» — опис подарунка; після дедлайну подарунка вже немає."""
        user = await self._known_user(profile)
        if user is None:
            return
        now = self.clock.now()
        if user.is_expired(now) or not self.content.messages.get("gift"):
            await self._send_expired(user, now)
            return
        await self.storage.add_event(user.user_id, EVENT_GIFT, now=now)
        await self._send(user, self.texts.gift(user, now), self.texts.keyboard("gift", user, now))

    async def show_course_info(self, profile: Profile) -> None:
        """«📖 Інформація про курс» — ще раз опис курсу (текст оферу), щоб не гортати чат угору."""
        user = await self._known_user(profile)
        if user is None:
            return
        now = self.clock.now()
        if not self.texts.packages_open(user, now):
            await self._send_expired(user, now)
            return
        await self.storage.add_event(user.user_id, EVENT_COURSE_INFO, now=now)
        await self._send(user, self.texts.course_info(user, now), self.texts.keyboard("course_info", user, now))

    async def send_preview(self, chat_id: int, first_name: str) -> None:
        """Для адміністратора: нагадування, подарунок і завершення — так, як їх побачать люди."""
        now = self.clock.now()
        person = _PreviewPerson(chat_id, first_name, None, now + self.content.settings.offer_duration)
        intro = ("👀 <b>Попередній перегляд</b>\nТак люди побачать нагадування, подарунок і завершення пропозиції. "
                 "Залишок часу — як у момент, коли нагадування прийде.")
        await self.sender.send(chat_id, intro, user_chat=False)
        for reminder in self.content.reminders:
            at = reminder.due_at(now, person.deadline_at)
            header = f"<i>Нагадування {reminder.id} — {reminder.describe()}:</i>\n\n"
            await self.sender.send(chat_id, header + self.texts.reminder(reminder, person, at),
                                   self.texts.keyboard("reminder", person, at), user_chat=False)
        if self.content.messages.get("gift"):
            await self.sender.send(chat_id, "<i>Кнопка «🎁» — опис подарунка:</i>\n\n" + self.texts.gift(person, now),
                                   self.texts.keyboard("gift", person, now), user_chat=False)
        await self.sender.send(chat_id, "<i>Після завершення — у повідомленні з таймером:</i>\n\n"
                               + self.texts.timer_line(person, person.deadline_at), user_chat=False)
        await self.sender.send(chat_id, "<i>Після завершення — на /start і будь-яку кнопку:</i>\n\n"
                               + self.texts.expired(person, person.deadline_at),
                               self.texts.keyboard("expired", person, person.deadline_at), user_chat=False)

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

    def is_active(self, user: UserRecord, now: int) -> bool:
        """Людина щойно щось робила в чаті (/start, кнопка, повідомлення) — швидкий відлік."""
        settings = self.content.settings
        return bool(settings.timer_fast_every) and now - user.last_seen_at < settings.timer_fast_for

    def timer_interval(self, user: UserRecord, now: int) -> int:
        """Як часто зараз оновлювати таймер цієї людини (секунд; 0 — не оновлювати)."""
        settings = self.content.settings
        if not settings.timer_update_every:
            return 0
        return settings.timer_fast_every if self.is_active(user, now) else settings.timer_update_every

    async def refresh_timer(self, user: UserRecord, now: int) -> Outcome | None:
        """Оновлює живий таймер в офері, якщо настав час і показане значення змінилось. None — оновлювати не треба.

        Поки людина активна в чаті, таймер оновлюється кожні timer_fast_every (видно, як біжать секунди),
        інакше — кожні timer_update_every.
        """
        every = self.timer_interval(user, now)
        if not every or user.offer_message_id is None:
            return None
        key = (user.user_id, user.offer_message_id)
        cached = self.timer_cache.get(key)
        if cached is not None and now - cached[1] < every:
            return None
        if now < self.timer_pause.get(user.chat_id, 0):
            return None
        text = self.texts.live_offer(user, now, has_body=user.offer_body_message_id is not None, step=timer_step(every))
        if cached is not None and cached[0] == text:
            return None
        result = await self.sender.edit(
            user.chat_id, user.offer_message_id, text, self.texts.keyboard("offer", user, now),
            background=True, wait_on_flood=False,
        )
        if result.ok:
            self.timer_cache[key] = (text, now)
        elif result.outcome is Outcome.GONE:  # людина видалила повідомлення — більше його не чіпаємо
            await self.storage.clear_offer_message(user.user_id, user.offer_message_id)
            self.timer_cache.pop(key, None)
        elif result.outcome is Outcome.RETRY and result.retry_after:
            # Telegram попросив зачекати: цей чат пропускаємо, решта таймерів оновлюється як звичайно
            self.timer_pause[user.chat_id] = now + result.retry_after
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

    async def _send_package(self, user: UserRecord, package: Package, now: int) -> None:
        """Опис пакета: з картинкою — фото з описом у підписі (або фото, а потім опис, якщо опис задовгий)."""
        text = self.texts.package_description(package, user, now)
        keyboard = self.texts.keyboard("package", user, now, package)
        if not package.image:
            await self._send(user, text, keyboard)
            return
        fits = len(strip_tags(text)) <= PHOTO_CAPTION_LIMIT
        photo = await self._send_photo(user, package.image, text if fits else "", keyboard if fits else None)
        if photo.outcome is Outcome.BLOCKED:
            return
        if not photo.ok:  # картинку надіслати не вдалося — опис усе одно має дійти
            log.warning("Не вдалося надіслати картинку пакета %s — надсилаю опис без неї", package.id)
            await self._send(user, text, keyboard)
        elif not fits:
            await self._send(user, text, keyboard)

    async def _send_photo(
        self, user: UserRecord, image: str, caption: str, keyboard: InlineKeyboardMarkup | None
    ) -> Result:
        cached = self.photo_ids.get(image)
        source = cached or (image if image.startswith(("http://", "https://")) else FSInputFile(project_path(image)))
        result = await self.sender.send_photo(user.chat_id, source, caption, keyboard)
        if cached and result.outcome is Outcome.FAILED:  # збережений id міг застаріти — надсилаємо файл заново
            self.photo_ids.pop(image, None)
            return await self._send_photo(user, image, caption, keyboard)
        if result.ok and result.file_id:
            self.photo_ids[image] = result.file_id
        return result

    async def _send_expired(self, user: UserRecord, now: int) -> Result:
        return await self._send(user, self.texts.expired(user, now), self.texts.keyboard("expired", user, now))

    async def _notify_manager(self, package: Package, user: UserRecord, now: int) -> None:
        if self.manager_chat_id is None:
            return
        await self.sender.send(
            self.manager_chat_id, self.texts.manager_payment_request(package, user, now), user_chat=False
        )
