"""Тексти і клавіатури для конкретного користувача в конкретний момент.

Усі шаблони беруться з content.yaml. Значення, що приходять ззовні (ім'я користувача) або є звичайним
текстом (назва і ціна пакета), екрануються перед вставкою в HTML.
"""

from __future__ import annotations

from typing import Protocol

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .content import (
    CHOOSE_PACKAGE,
    COURSE_INFO,
    GET_PAYMENT_LINK,
    GIFT,
    OTHER_PACKAGES,
    PAY_URL,
    Content,
    Package,
    Reminder,
)
from .htmltext import esc, fill
from .timeutils import format_countdown, format_deadline, format_progress, format_time_left


def _tidy(text: str) -> str:
    """Прибирає пробіли в кінці рядків (наприклад, коли порожня підстановка стоїть останньою)."""
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()

# callback_data кнопок (до 64 байтів; id пакетів — до 32 символів)
CB_PACKAGES = "packages"
CB_PACKAGE = "pkg:"
CB_PAY = "pay:"
CB_GIFT = "gift"
CB_COURSE = "course"


class Person(Protocol):
    """Мінімум даних про користувача для текстів (storage.UserRecord цьому відповідає)."""

    user_id: int
    first_name: str
    username: str | None
    deadline_at: int


class Texts:
    def __init__(self, content: Content) -> None:
        self.content = content
        self.settings = content.settings

    # ------------------------------------------------------------------ стан пропозиції

    @staticmethod
    def is_expired(user: Person, now: int) -> bool:
        return now >= user.deadline_at

    def packages_open(self, user: Person, now: int) -> bool:
        """Чи можна зараз показувати пакети й посилання на оплату."""
        return not self.is_expired(user, now) or self.settings.packages_after_expiry

    def time_left(self, user: Person, now: int) -> str:
        return format_time_left(user.deadline_at - now, self.settings.time_left_format)

    def deadline(self, user: Person) -> str:
        return format_deadline(user.deadline_at, self.settings.timezone, self.settings.deadline_format)

    def countdown(self, user: Person, now: int, step: int = 1) -> str:
        """Відлік «23:59:55». step — крок оновлення живого таймера (значення кратні кроку)."""
        return format_countdown(user.deadline_at - now, self.settings.countdown_format, step)

    def progress(self, user: Person, now: int) -> str:
        """Смужка залишку часу «🟩🟩🟩🟩🟩🟩🟩⬜️⬜️⬜️» (частка від тривалості пропозиції)."""
        s = self.settings
        return format_progress(user.deadline_at - now, s.offer_duration, s.progress_length, s.progress_full, s.progress_empty)

    # ------------------------------------------------------------------ тексти

    def _common(self, user: Person, now: int, step: int = 1) -> dict[str, str]:
        return {
            "first_name": esc(user.first_name),
            "time_left": esc(self.time_left(user, now)),
            "deadline": esc(self.deadline(user)),
            "countdown": esc(self.countdown(user, now, step)),
            "progress": esc(self.progress(user, now)),
        }

    def _package_values(self, package: Package, user: Person, now: int) -> dict[str, str]:
        expired = self.is_expired(user, now)
        values = self._common(user, now)
        values.update(
            name=esc(package.name),
            price=esc(package.price_for(expired)),
            payment_url=esc(package.url_for(expired) or ""),
        )
        return values

    def timer_line(self, user: Person, now: int, step: int = 1) -> str:
        key = "timer_expired" if self.is_expired(user, now) else "timer"
        return _tidy(fill(self.content.messages[key], self._common(user, now, step)))

    def offer(self, user: Person, now: int, step: int = 1) -> str:
        values = self._common(user, now, step)
        values["timer"] = self.timer_line(user, now, step)
        return _tidy(fill(self.content.messages["offer"], values))

    @property
    def separate_timer(self) -> bool:
        """Таймер — окремим повідомленням під офером (messages.offer_timer задано)."""
        return bool(self.content.messages.get("offer_timer"))

    def offer_timer(self, user: Person, now: int, step: int = 1) -> str:
        values = self._common(user, now, step)
        values["timer"] = self.timer_line(user, now, step)
        return _tidy(fill(self.content.messages["offer_timer"], values))

    def live_offer(self, user: Person, now: int, *, has_body: bool, step: int = 1) -> str:
        """Текст повідомлення з живим таймером. step — крок оновлення таймера (для {countdown}).

        has_body — офер цього користувача надіслано двома повідомленнями (текст окремо, таймер окремо).
        Якщо офер колись надіслано одним повідомленням, а тепер таймер окремий, — оновлюємо те одне
        повідомлення повністю: текст оферу + таймер (щоб не затерти текст).
        """
        if has_body:
            return self.offer_timer(user, now, step)
        if self.separate_timer:
            return self.offer(user, now, step) + "\n\n" + self.offer_timer(user, now, step)
        return self.offer(user, now, step)

    def packages_list(self, user: Person, now: int) -> str:
        expired = self.is_expired(user, now)
        lines = [
            fill(
                self.content.messages["package_line"],
                {"number": str(number), "name": esc(package.name), "price": esc(package.price_for(expired))},
            )
            for number, package in enumerate(self.content.packages, start=1)
        ]
        values = self._common(user, now)
        values["packages"] = "\n".join(lines)
        return _tidy(fill(self.content.messages["packages_list"], values))

    def package_description(self, package: Package, user: Person, now: int) -> str:
        return _tidy(fill(package.description, self._package_values(package, user, now)))

    def payment(self, package: Package, user: Person, now: int) -> str:
        return _tidy(fill(self.content.messages["payment"], self._package_values(package, user, now)))

    def expired(self, user: Person, now: int) -> str:
        """Відповідь після завершення пропозиції. Порожній messages.expired — опис курсу (як кнопка course_info)."""
        template = self.content.messages["expired"]
        if not template:
            return self.course_info(user, now)
        return _tidy(fill(template, self._common(user, now)))

    def expired_notice(self, user: Person, now: int) -> str:
        return _tidy(fill(self.content.messages["expired_notice"], self._common(user, now)))

    def fallback(self, user: Person, now: int) -> str:
        """Відповідь на довільне повідомлення ("" — не відповідати)."""
        return _tidy(fill(self.content.messages["fallback"], self._common(user, now)))

    def reminder(self, reminder: Reminder, user: Person, now: int) -> str:
        return _tidy(fill(reminder.text, self._common(user, now)))

    def gift(self, user: Person, now: int) -> str:
        return _tidy(fill(self.content.messages["gift"], self._common(user, now)))

    def course_info(self, user: Person, now: int) -> str:
        """«📖 Інформація про курс»: messages.course_info, а якщо він порожній — ще раз текст оферу."""
        template = self.content.messages.get("course_info")
        if template:
            return _tidy(fill(template, self._common(user, now)))
        return self.offer(user, now)

    def package_unavailable(self) -> str:
        return self.content.messages["package_unavailable"]

    def manager_payment_request(self, package: Package, user: Person, now: int) -> str:
        expired = self.is_expired(user, now)
        values = self._common(user, now)
        name = user.first_name or "Користувач"
        values.update(
            user=f'<a href="tg://user?id={int(user.user_id)}">{esc(name)}</a>',
            username=esc(f"@{user.username}") if user.username else "",
            user_id=str(int(user.user_id)),
            package=esc(package.name),
            price=esc(package.price_for(expired)),
        )
        return _tidy(fill(self.content.messages["manager_payment_request"], values))

    # ------------------------------------------------------------------ клавіатури

    def keyboard(self, name: str, user: Person, now: int, package: Package | None = None) -> InlineKeyboardMarkup | None:
        """Клавіатура з keyboards.<name>. Коли пакети недоступні (після дедлайну), лишається лише course_info.

        Кнопки оплати показуються лише для пакета з посиланням на оплату (інформаційний пакет — без них).
        """
        packages_open = self.packages_open(user, now)
        expired = self.is_expired(user, now)
        buttons_text = self.content.buttons
        rows: list[list[InlineKeyboardButton]] = []
        for row in self.content.keyboards[name]:
            buttons: list[InlineKeyboardButton] = []
            for button_id in row:
                if button_id in (CHOOSE_PACKAGE, OTHER_PACKAGES):
                    if packages_open:
                        buttons.append(InlineKeyboardButton(text=buttons_text[button_id], callback_data=CB_PACKAGES))
                elif button_id == GET_PAYMENT_LINK:
                    if packages_open and package is not None and package.url_for(expired):
                        buttons.append(
                            InlineKeyboardButton(text=buttons_text[button_id], callback_data=CB_PAY + package.id)
                        )
                elif button_id == PAY_URL:
                    if packages_open and package is not None and (url := package.url_for(expired)):
                        buttons.append(InlineKeyboardButton(text=buttons_text[button_id], url=url))
                elif button_id == GIFT and not expired and self.content.messages.get("gift"):
                    buttons.append(InlineKeyboardButton(text=buttons_text[button_id], callback_data=CB_GIFT))
                elif button_id == COURSE_INFO:
                    buttons.append(InlineKeyboardButton(text=buttons_text[button_id], callback_data=CB_COURSE))
            if buttons:
                rows.append(buttons)
        return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

    def packages_keyboard(self, user: Person, now: int) -> InlineKeyboardMarkup:
        """Кнопки пакетів у порядку з content.yaml: «назва — ціна»; під ними — ряди з keyboards.packages_list."""
        expired = self.is_expired(user, now)
        buttons = [
            InlineKeyboardButton(
                text=fill(self.content.buttons["package"], {"name": package.name, "price": package.price_for(expired)}),
                callback_data=CB_PACKAGE + package.id,
            )
            for package in self.content.packages
        ]
        per_row = self.settings.packages_per_row
        rows = [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]
        extra = self.keyboard("packages_list", user, now)
        if extra is not None:
            rows += extra.inline_keyboard
        return InlineKeyboardMarkup(inline_keyboard=rows)
