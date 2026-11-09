"""Завантаження і перевірка content.yaml.

Усе, що бачить користувач, береться звідси. Помилки збираються всі одразу і пояснюються українською
з шляхом до поля, щоб редактор контенту міг виправити файл без програміста.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from .htmltext import has_tags, placeholders, prepare_html, strip_tags
from .timeutils import (
    DEADLINE_PLACEHOLDERS,
    TIME_LEFT_PLACEHOLDERS,
    DurationError,
    SilentHours,
    format_duration,
    parse_duration,
    parse_silent_hours,
)

TELEGRAM_TEXT_LIMIT = 4096
TEMPLATE_TEXT_LIMIT = 3900
CALLBACK_ALERT_LIMIT = 200

# ----------------------------------------------------------------------------- кнопки і клавіатури

CHOOSE_PACKAGE = "choose_package"
OTHER_PACKAGES = "other_packages"
GET_PAYMENT_LINK = "get_payment_link"
PAY_URL = "pay_url"
BUTTON_IDS = (CHOOSE_PACKAGE, OTHER_PACKAGES, GET_PAYMENT_LINK, PAY_URL)
# Кнопки, які ведуть до пакетів або оплати (після завершення пропозиції вони зникають,
# якщо settings.packages_after_expiry: false).
PACKAGE_BUTTONS = frozenset(BUTTON_IDS)

_LIST_BUTTONS = frozenset({CHOOSE_PACKAGE, OTHER_PACKAGES})
# Клавіатура → (дозволені кнопки, значення за замовчуванням)
KEYBOARDS: dict[str, tuple[frozenset[str], tuple[tuple[str, ...], ...]]] = {
    "offer": (_LIST_BUTTONS, ((CHOOSE_PACKAGE,),)),
    "package": (_LIST_BUTTONS | {GET_PAYMENT_LINK}, ((GET_PAYMENT_LINK,), (OTHER_PACKAGES,))),
    "payment": (_LIST_BUTTONS | {PAY_URL}, ((PAY_URL,), (OTHER_PACKAGES,))),
    "reminder": (_LIST_BUTTONS, ((CHOOSE_PACKAGE,),)),
    "fallback": (_LIST_BUTTONS, ((CHOOSE_PACKAGE,),)),
    "expired": (_LIST_BUTTONS, ()),
}

# ----------------------------------------------------------------------------- тексти

COMMON_PLACEHOLDERS = frozenset({"first_name", "time_left", "deadline"})
PACKAGE_PLACEHOLDERS = COMMON_PLACEHOLDERS | {"name", "price", "payment_url"}

# Повідомлення (Telegram-HTML): назва → (обов'язкове, дозволені підстановки, значення за замовчуванням)
MESSAGES: dict[str, tuple[bool, frozenset[str], str | None]] = {
    "offer": (True, COMMON_PLACEHOLDERS | {"timer"}, None),
    "timer": (True, frozenset({"time_left", "deadline"}), None),
    "timer_expired": (True, frozenset({"deadline"}), None),
    "packages_list": (True, COMMON_PLACEHOLDERS | {"packages"}, None),
    "package_line": (False, frozenset({"number", "name", "price"}), "{number}. <b>{name}</b> — {price}"),
    "payment": (True, PACKAGE_PLACEHOLDERS, None),
    "expired": (True, COMMON_PLACEHOLDERS, None),
    "expired_notice": (False, COMMON_PLACEHOLDERS, "⌛️ Пропозиція для рішучих завершилась."),
    "fallback": (False, COMMON_PLACEHOLDERS, ""),
    "manager_payment_request": (
        False,
        frozenset({"user", "username", "user_id", "package", "price", "time_left", "deadline"}),
        "💳 <b>Запит посилання на оплату</b>\nПакет: {package} — {price}\n"
        "Користувач: {user} {username}\nID: <code>{user_id}</code>\nДо кінця пропозиції: {time_left}",
    ),
}
# Повідомлення, які можуть бути порожніми ("" = не надсилати).
OPTIONAL_EMPTY_MESSAGES = frozenset({"fallback"})
# Звичайний текст (без HTML): назва → значення за замовчуванням
PLAIN_MESSAGES: dict[str, str] = {
    "package_unavailable": "Цей пакет більше недоступний. Оберіть інший 👇",
}

# Підписи кнопок (звичайний текст): назва → (дозволені підстановки, значення за замовчуванням)
BUTTON_TEXTS: dict[str, tuple[frozenset[str], str | None]] = {
    CHOOSE_PACKAGE: (frozenset(), None),
    OTHER_PACKAGES: (frozenset(), None),
    GET_PAYMENT_LINK: (frozenset(), None),
    PAY_URL: (frozenset(), None),
    "package": (frozenset({"name", "price"}), "{name} — {price}"),
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "offer_duration": "24 год",
    "timezone": "Europe/Kyiv",
    "deadline_format": "{day} {month_name} о {time}",
    "time_left_format": "auto",
    "timer_update_every": "1 хв",
    "packages_after_expiry": False,
    "send_expired_notice": False,
    "silent_hours": "22:00-08:00",
    "packages_per_row": 1,
    "link_previews": False,
    "start_command_description": "Отримати пропозицію",
}

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_ID_RULE = "лише малі латинські літери, цифри, «_» і «-», до 32 символів (наприклад «vip»)"
_SECTIONS = ("settings", "messages", "buttons", "keyboards", "packages", "reminders")


# ----------------------------------------------------------------------------- моделі


@dataclass(frozen=True)
class Package:
    id: str
    name: str
    price: str
    description: str
    payment_url: str
    price_after_expiry: str | None = None
    payment_url_after_expiry: str | None = None

    def price_for(self, expired: bool) -> str:
        return self.price_after_expiry if expired and self.price_after_expiry else self.price

    def url_for(self, expired: bool) -> str:
        return self.payment_url_after_expiry if expired and self.payment_url_after_expiry else self.payment_url


@dataclass(frozen=True)
class Reminder:
    id: str
    text: str
    after: int | None = None  # секунд після першого /start
    before_end: int | None = None  # секунд до кінця пропозиції

    def due_at(self, started_at: int, deadline_at: int) -> int:
        """Коли надіслати нагадування цьому користувачу (unix-секунди)."""
        if self.after is not None:
            return started_at + self.after
        return deadline_at - (self.before_end or 0)

    def describe(self) -> str:
        if self.after is not None:
            return f"через {format_duration(self.after)} після старту"
        return f"за {format_duration(self.before_end or 0)} до кінця"


@dataclass(frozen=True)
class ContentSettings:
    offer_duration: int
    timezone: ZoneInfo
    deadline_format: str
    time_left_format: str
    timer_update_every: int
    packages_after_expiry: bool
    send_expired_notice: bool
    silent_hours: SilentHours | None
    packages_per_row: int
    link_previews: bool
    start_command_description: str


@dataclass(frozen=True)
class Content:
    settings: ContentSettings
    messages: Mapping[str, str]
    buttons: Mapping[str, str]
    keyboards: Mapping[str, tuple[tuple[str, ...], ...]]
    packages: tuple[Package, ...]
    reminders: tuple[Reminder, ...]
    warnings: tuple[str, ...] = ()
    source: str = "content.yaml"
    _by_id: Mapping[str, Package] = field(default_factory=dict, repr=False, compare=False)

    def package(self, package_id: str) -> Package | None:
        return self._by_id.get(package_id)


class ContentError(Exception):
    """У content.yaml є помилки, через які бот не може працювати."""

    def __init__(self, errors: list[str], warnings: list[str] | tuple[str, ...] = ()) -> None:
        self.errors = list(errors)
        self.warnings = list(warnings)
        super().__init__("\n".join(self.errors))

    def report(self) -> str:
        lines = [f"Помилок у файлі контенту: {len(self.errors)}"]
        lines += [f"  • {e}" for e in self.errors]
        return "\n".join(lines)


# ----------------------------------------------------------------------------- перевірка


class _Checker:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    def warn(self, path: str, message: str) -> None:
        self.warnings.append(f"{path}: {message}")

    # --- примітиви

    def text(self, path: str, value: Any, *, required: bool = True) -> str | None:
        """Будь-який текст. Числа перетворюються на текст («price: 1990» → «1990»)."""
        if value is None:
            if required:
                self.error(path, "поле відсутнє або порожнє")
            return None
        if isinstance(value, bool):
            self.error(path, "YAML прочитав значення як так/ні — візьміть текст у лапки, наприклад \"Так\"")
            return None
        if isinstance(value, (int, float)):
            return str(value)
        if not isinstance(value, str):
            self.error(path, "очікується текст (а не список чи розділ)")
            return None
        return value.strip()

    def plain(self, path: str, value: Any, allowed: frozenset[str] = frozenset(), *, required: bool = True,
              max_len: int | None = None) -> str | None:
        """Звичайний текст без HTML (назви, ціни, підписи кнопок)."""
        text = self.text(path, value, required=required)
        if text is None:
            return None
        if not text:
            if required:
                self.error(path, "текст порожній")
            return None
        if has_tags(text):
            self.error(path, "HTML-теги тут не підтримуються — лише звичайний текст")
        self.check_placeholders(path, text, allowed)
        if max_len is not None and len(text) > max_len:
            self.error(path, f"занадто довго: {len(text)} символів, максимум {max_len}")
        return text

    def html(self, path: str, value: Any, allowed: frozenset[str], *, required: bool = True,
             allow_empty: bool = False) -> str | None:
        """Текст повідомлення у форматі Telegram-HTML."""
        text = self.text(path, value, required=required)
        if text is None:
            return None
        if not text and not allow_empty:
            self.error(path, "текст порожній")
            return None
        checked = prepare_html(text)
        for problem in checked.errors:
            self.error(path, problem)
        self.check_placeholders(path, text, allowed)
        visible = len(strip_tags(checked.text))
        if visible > TEMPLATE_TEXT_LIMIT:
            self.error(
                path,
                f"занадто довгий текст: {visible} символів. Максимум — {TEMPLATE_TEXT_LIMIT} "
                f"(ліміт Telegram {TELEGRAM_TEXT_LIMIT}, частину місця займають підстановки)",
            )
        return checked.text

    def check_placeholders(self, path: str, text: str, allowed: frozenset[str]) -> None:
        for name in sorted(placeholders(text) - allowed):
            available = ", ".join("{%s}" % a for a in sorted(allowed)) or "немає"
            self.warn(path, f"невідома підстановка {{{name}}} — її буде показано як є. Тут доступні: {available}")

    def boolean(self, path: str, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        self.error(path, "очікується true (так) або false (ні)")
        return False

    def duration(self, path: str, value: Any) -> int | None:
        try:
            return parse_duration(value)
        except DurationError as e:
            self.error(path, str(e))
            return None

    def url(self, path: str, value: Any, *, required: bool = True) -> str | None:
        text = self.text(path, value, required=required)
        if not text:
            if required and text is not None:
                self.error(path, "посилання порожнє")
            return None
        parsed = urlparse(text)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or any(c.isspace() for c in text):
            self.error(path, f"«{text}» не схоже на посилання — воно має починатися з https://")
            return None
        host = (parsed.hostname or "").lower()
        if "." not in host.strip(".") or "…" in text or "✏" in text:
            self.error(path, f"«{text}» схоже на заготовку, а не на справжнє посилання — вставте повне посилання "
                             "з вашої платіжної системи")
            return None
        if host == "example.com" or host.endswith(".example.com"):
            self.warn(path, "це посилання-заглушка (example.com) — замініть на справжнє посилання на оплату")
        return text

    def item_id(self, path: str, value: Any, seen: set[str]) -> str | None:
        if isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str) or not _ID_RE.match(value):
            self.error(path, f"id: {_ID_RULE}")
            return None
        if value in seen:
            self.error(path, f"id «{value}» уже використано — id мають бути унікальними")
            return None
        seen.add(value)
        return value

    def unknown_keys(self, path: str, raw: Mapping[str, Any], known: set[str] | frozenset[str]) -> None:
        for key in raw:
            if key not in known:
                self.warn(f"{path}.{key}" if path else str(key), "невідоме поле — ігнорується (перевірте, чи немає помилки в назві)")

    # --- розділи

    def settings(self, raw: Any) -> ContentSettings | None:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            self.error("settings", "очікується розділ з параметрами")
            raw = {}
        self.unknown_keys("settings", raw, set(DEFAULT_SETTINGS))

        def get(key: str) -> Any:
            # Немає параметра — значення за замовчуванням. Порожній silent_hours — тихі години вимкнено.
            if key not in raw:
                return DEFAULT_SETTINGS[key]
            value = raw[key]
            return DEFAULT_SETTINGS[key] if value is None and key != "silent_hours" else value

        p = "settings."
        duration = self.duration(p + "offer_duration", get("offer_duration"))
        if duration is not None and not (60 <= duration <= 30 * 86400):
            self.error(p + "offer_duration", "тривалість має бути від 1 хв до 30 днів")
            duration = None

        tz_name = self.text(p + "timezone", get("timezone"))
        tz: ZoneInfo | None = None
        if tz_name:
            try:
                tz = ZoneInfo(tz_name)
            except (ZoneInfoNotFoundError, ValueError):
                self.error(p + "timezone", f"невідомий часовий пояс «{tz_name}», приклад: Europe/Kyiv")

        deadline_format = self.plain(p + "deadline_format", get("deadline_format"), DEADLINE_PLACEHOLDERS)

        time_left_format = self.plain(p + "time_left_format", get("time_left_format"), TIME_LEFT_PLACEHOLDERS)
        if time_left_format is not None and time_left_format != "auto" and not (
            placeholders(time_left_format) & TIME_LEFT_PLACEHOLDERS
        ):
            self.error(p + "time_left_format", "вкажіть auto або шаблон з {h}, {hh}, {m}, {mm}, наприклад «{h} год {mm} хв»")

        update_every = self.duration(p + "timer_update_every", get("timer_update_every"))
        if update_every is not None and 0 < update_every < 60:
            self.error(p + "timer_update_every", "щонайменше 1 хв (або 0, щоб не оновлювати таймер)")

        after_expiry = self.boolean(p + "packages_after_expiry", get("packages_after_expiry"))
        expired_notice = self.boolean(p + "send_expired_notice", get("send_expired_notice"))
        link_previews = self.boolean(p + "link_previews", get("link_previews"))

        silent: SilentHours | None = None
        try:
            silent = parse_silent_hours(get("silent_hours"))
        except DurationError as e:
            self.error(p + "silent_hours", str(e))

        per_row = get("packages_per_row")
        if isinstance(per_row, bool) or not isinstance(per_row, int) or not 1 <= per_row <= 8:
            self.error(p + "packages_per_row", "ціле число від 1 до 8")
            per_row = 1

        start_description = self.plain(
            p + "start_command_description", get("start_command_description"), max_len=256
        )

        if None in (duration, tz, deadline_format, time_left_format, update_every, start_description):
            return None
        return ContentSettings(
            offer_duration=duration,  # type: ignore[arg-type]
            timezone=tz,  # type: ignore[arg-type]
            deadline_format=deadline_format,  # type: ignore[arg-type]
            time_left_format=time_left_format,  # type: ignore[arg-type]
            timer_update_every=update_every,  # type: ignore[arg-type]
            packages_after_expiry=after_expiry,
            send_expired_notice=expired_notice,
            silent_hours=silent,
            packages_per_row=per_row,
            link_previews=link_previews,
            start_command_description=start_description,  # type: ignore[arg-type]
        )

    def messages(self, raw: Any) -> dict[str, str]:
        if not isinstance(raw, dict):
            self.error("messages", "розділ відсутній або має неправильний формат")
            raw = {}
        self.unknown_keys("messages", raw, set(MESSAGES) | set(PLAIN_MESSAGES))
        result: dict[str, str] = {}
        for key, (required, allowed, default) in MESSAGES.items():
            path = f"messages.{key}"
            value = raw.get(key)
            if value is None and not required:
                value = default
            text = self.html(path, value, allowed, allow_empty=key in OPTIONAL_EMPTY_MESSAGES)
            if text is not None:
                result[key] = text
        for key, default in PLAIN_MESSAGES.items():
            value = raw.get(key)
            text = self.plain(f"messages.{key}", default if value is None else value, max_len=CALLBACK_ALERT_LIMIT)
            if text is not None:
                result[key] = text
        offer = result.get("offer")
        if offer is not None and "timer" not in placeholders(offer):
            self.warn("messages.offer", "немає підстановки {timer} — таймер в офері не буде показано")
        return result

    def buttons(self, raw: Any) -> dict[str, str]:
        if not isinstance(raw, dict):
            self.error("buttons", "розділ відсутній або має неправильний формат")
            raw = {}
        self.unknown_keys("buttons", raw, set(BUTTON_TEXTS))
        result: dict[str, str] = {}
        for key, (allowed, default) in BUTTON_TEXTS.items():
            value = raw.get(key)
            if value is None and default is not None:
                value = default
            text = self.plain(f"buttons.{key}", value, allowed)
            if text is not None:
                if len(text) > 64:
                    self.warn(f"buttons.{key}", "довгий підпис — на телефоні його може бути обрізано")
                result[key] = text
        return result

    def keyboards(self, raw: Any) -> dict[str, tuple[tuple[str, ...], ...]]:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            self.error("keyboards", "розділ має неправильний формат")
            raw = {}
        self.unknown_keys("keyboards", raw, set(KEYBOARDS))
        result: dict[str, tuple[tuple[str, ...], ...]] = {}
        for name, (allowed, default) in KEYBOARDS.items():
            path = f"keyboards.{name}"
            value = raw.get(name, None)
            if name not in raw:
                result[name] = default
                continue
            if value is None or value == []:
                result[name] = ()
                continue
            if not isinstance(value, list):
                self.error(path, "очікується список рядів кнопок, наприклад:  - [choose_package]")
                continue
            rows: list[tuple[str, ...]] = []
            used: set[str] = set()
            for i, row in enumerate(value, start=1):
                if isinstance(row, str):
                    row = [row]
                if not isinstance(row, list) or not row:
                    self.error(f"{path}[{i}]", "ряд має бути списком кнопок, наприклад [choose_package]")
                    continue
                buttons: list[str] = []
                for button in row:
                    if button not in BUTTON_IDS:
                        self.error(f"{path}[{i}]", f"невідома кнопка «{button}». Доступні: " + ", ".join(sorted(allowed)))
                    elif button not in allowed:
                        self.error(
                            f"{path}[{i}]",
                            f"кнопку {button} тут використати не можна. Тут доступні: " + ", ".join(sorted(allowed)),
                        )
                    elif button in used:
                        self.warn(f"{path}[{i}]", f"кнопка {button} повторюється")
                    else:
                        used.add(button)
                        buttons.append(button)
                if buttons:
                    rows.append(tuple(buttons))
            result[name] = tuple(rows)
        return result

    def packages(self, raw: Any) -> tuple[Package, ...]:
        if not isinstance(raw, list) or not raw:
            self.error("packages", "потрібен хоча б один пакет (список, кожен пакет починається з «- id: …»)")
            return ()
        known = {"id", "name", "price", "description", "payment_url", "price_after_expiry", "payment_url_after_expiry"}
        seen: set[str] = set()
        result: list[Package] = []
        for i, item in enumerate(raw, start=1):
            path = f"packages[{i}]"
            if not isinstance(item, dict):
                self.error(path, "пакет має бути розділом з полями id, name, price, description, payment_url")
                continue
            package_id = self.item_id(path, item.get("id"), seen)
            if package_id:
                path = f"packages[{i}] ({package_id})"
            self.unknown_keys(path, item, known)
            name = self.plain(f"{path}.name", item.get("name"))
            price = self.plain(f"{path}.price", item.get("price"))
            description = self.html(f"{path}.description", item.get("description"), PACKAGE_PLACEHOLDERS)
            payment_url = self.url(f"{path}.payment_url", item.get("payment_url"))
            price_after = self.plain(f"{path}.price_after_expiry", item.get("price_after_expiry"), required=False)
            url_after = self.url(f"{path}.payment_url_after_expiry", item.get("payment_url_after_expiry"), required=False)
            if None in (package_id, name, price, description, payment_url):
                continue
            result.append(
                Package(
                    id=package_id,  # type: ignore[arg-type]
                    name=name,  # type: ignore[arg-type]
                    price=price,  # type: ignore[arg-type]
                    description=description,  # type: ignore[arg-type]
                    payment_url=payment_url,  # type: ignore[arg-type]
                    price_after_expiry=price_after,
                    payment_url_after_expiry=url_after,
                )
            )
        return tuple(result)

    def reminders(self, raw: Any, duration: int | None) -> tuple[Reminder, ...]:
        if raw is None or raw == []:
            return ()
        if not isinstance(raw, list):
            self.error("reminders", "очікується список нагадувань (кожне починається з «- id: …»)")
            return ()
        seen: set[str] = set()
        result: list[Reminder] = []
        for i, item in enumerate(raw, start=1):
            path = f"reminders[{i}]"
            if not isinstance(item, dict):
                self.error(path, "нагадування має бути розділом з полями id, after або before_end, text")
                continue
            reminder_id = self.item_id(path, item.get("id"), seen)
            if reminder_id:
                path = f"reminders[{i}] ({reminder_id})"
            self.unknown_keys(path, item, {"id", "after", "before_end", "text"})
            has_after, has_before = item.get("after") is not None, item.get("before_end") is not None
            after = before = None
            if has_after == has_before:
                self.error(path, "вкажіть рівно одне з двох: after (через скільки після старту) "
                                 "або before_end (за скільки до кінця)")
            elif has_after:
                after = self.duration(f"{path}.after", item.get("after"))
                if after is not None and duration is not None and not 0 < after < duration:
                    self.error(f"{path}.after", f"має бути більше 0 і менше тривалості пропозиції ({format_duration(duration)})")
                    after = None
            else:
                before = self.duration(f"{path}.before_end", item.get("before_end"))
                if before is not None and duration is not None and not 0 < before < duration:
                    self.error(f"{path}.before_end", f"має бути більше 0 і менше тривалості пропозиції ({format_duration(duration)})")
                    before = None
            text = self.html(f"{path}.text", item.get("text"), COMMON_PLACEHOLDERS)
            if reminder_id is None or text is None or (after is None and before is None):
                continue
            result.append(Reminder(id=reminder_id, text=text, after=after, before_end=before))
        if duration is not None:
            result.sort(key=lambda r: r.after if r.after is not None else duration - (r.before_end or 0))
            offsets: dict[int, str] = {}
            for r in result:
                offset = r.after if r.after is not None else duration - (r.before_end or 0)
                if offset in offsets:
                    self.warn("reminders", f"нагадування «{offsets[offset]}» і «{r.id}» припадають на один і той самий час")
                offsets[offset] = r.id
        return tuple(result)


def parse_content(raw: Any, source: str = "content.yaml") -> Content:
    """Перевіряє вже розібраний YAML і повертає Content або кидає ContentError з усіма помилками."""
    if not isinstance(raw, dict):
        raise ContentError(["файл має містити розділи: " + ", ".join(_SECTIONS)])
    c = _Checker()
    c.unknown_keys("", raw, set(_SECTIONS))
    settings = c.settings(raw.get("settings"))
    messages = c.messages(raw.get("messages"))
    buttons = c.buttons(raw.get("buttons"))
    keyboards = c.keyboards(raw.get("keyboards"))
    packages = c.packages(raw.get("packages"))
    reminders = c.reminders(raw.get("reminders"), settings.offer_duration if settings else None)

    if settings is not None and settings.packages_after_expiry:
        if not any(b in PACKAGE_BUTTONS for row in keyboards.get("expired", ()) for b in row):
            c.warn("keyboards.expired", "пакети доступні й після завершення (packages_after_expiry: true), але під "
                                        "повідомленням «пропозиція завершилась» немає кнопки — додайте [choose_package]")
    if settings is not None and not settings.packages_after_expiry:
        if any(b in PACKAGE_BUTTONS for row in keyboards.get("expired", ()) for b in row):
            c.warn("keyboards.expired", "після завершення пропозиції пакети недоступні "
                                        "(settings.packages_after_expiry: false), тому ці кнопки не показуватимуться")
        if any(p.price_after_expiry or p.payment_url_after_expiry for p in packages):
            c.warn("packages", "price_after_expiry / payment_url_after_expiry діють лише з "
                               "settings.packages_after_expiry: true")

    if c.errors or settings is None:
        raise ContentError(c.errors or ["settings: розділ містить помилки"], c.warnings)
    return Content(
        settings=settings,
        messages=messages,
        buttons=buttons,
        keyboards=keyboards,
        packages=packages,
        reminders=reminders,
        warnings=tuple(c.warnings),
        source=source,
        _by_id={p.id: p for p in packages},
    )


def _yaml_error(path: Path, error: yaml.YAMLError) -> str:
    mark = getattr(error, "problem_mark", None)
    where = f"рядок {mark.line + 1}, позиція {mark.column + 1}" if mark is not None else "невідоме місце"
    problem = getattr(error, "problem", None) or str(error)
    return (
        f"{path.name}: синтаксична помилка YAML ({where}): {problem}. Найчастіші причини: відступ не збігається "
        "з сусідніми рядками; текст із «: » або «#» без лапок; лапки не закрито"
    )


def load_content(path: str | Path) -> Content:
    """Читає і перевіряє файл контенту."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ContentError([f"файл контенту {path} не знайдено"]) from None
    except UnicodeDecodeError:
        raise ContentError([f"{path.name}: файл має бути збережений у кодуванні UTF-8"]) from None

    tab_lines = [n for n, line in enumerate(text.splitlines(), start=1) if "\t" in line[: len(line) - len(line.lstrip())]]
    if tab_lines:
        shown = ", ".join(map(str, tab_lines[:10]))
        raise ContentError([f"{path.name}: у відступах є символ Tab (рядки {shown}) — замініть його пробілами"])
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ContentError([_yaml_error(path, e)]) from None
    return parse_content(raw, source=path.name)
