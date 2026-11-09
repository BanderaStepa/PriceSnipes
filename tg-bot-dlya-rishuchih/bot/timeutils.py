"""Час: тривалості («24 год», «90 хв»), формат {time_left}, {countdown}, {progress} і {deadline}, тихі години, годинник."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .htmltext import fill

DURATION_EXAMPLES = "24 год, 90 хв, 1 год 30 хв, 2 д, 1h30m"

MONTHS_GENITIVE = (
    "січня", "лютого", "березня", "квітня", "травня", "червня",
    "липня", "серпня", "вересня", "жовтня", "листопада", "грудня",
)

DEADLINE_PLACEHOLDERS = frozenset({"day", "month", "month_name", "year", "time", "date"})
TIME_LEFT_PLACEHOLDERS = frozenset({"h", "hh", "m", "mm"})
COUNTDOWN_PLACEHOLDERS = frozenset({"h", "hh", "m", "mm", "s", "ss"})


class DurationError(ValueError):
    """Не вдалося розібрати тривалість або інтервал часу."""


class Clock:
    """Джерело поточного часу (UTC, цілі unix-секунди). У тестах підміняється."""

    def now(self) -> int:
        return int(time.time())


# ----------------------------------------------------------------------------- тривалості

_PART_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*([^\W\d_]+)\.?")
_SEPARATOR_RE = re.compile(r"[\s,;+]*")


def _unit_seconds(unit: str) -> int | None:
    u = unit.lower()
    if u in {"d", "day", "days", "д", "дн", "день", "дні", "днів", "доба", "доби", "діб"}:
        return 86400
    if u in {"h", "hr", "hrs", "hour", "hours", "г"} or u.startswith("год"):
        return 3600
    if u in {"m", "min", "mins", "minute", "minutes"} or u.startswith("хв"):
        return 60
    if u in {"s", "sec", "secs", "second", "seconds", "с"} or u.startswith("сек"):
        return 1
    return None


def parse_duration(value: object) -> int:
    """Тривалість у секундах. Приймає «24 год», «24 години», «90 хв», «1 год 30 хв», «1h30m», «2 д», 0."""
    if isinstance(value, bool) or value is None:
        raise DurationError(f"очікується тривалість, наприклад: {DURATION_EXAMPLES}")
    if isinstance(value, (int, float)):
        if value == 0:
            return 0
        raise DurationError(
            f"вкажіть одиниці виміру: «{value} год», «{value} хв» тощо (приклади: {DURATION_EXAMPLES})"
        )
    if not isinstance(value, str):
        raise DurationError(f"очікується тривалість, наприклад: {DURATION_EXAMPLES}")

    text = value.strip().lower()
    if text == "0":
        return 0
    total = 0.0
    parts = 0
    pos = 0
    while pos < len(text):
        pos = _SEPARATOR_RE.match(text, pos).end()  # type: ignore[union-attr]
        if pos >= len(text):
            break
        m = _PART_RE.match(text, pos)
        if m is None:
            raise DurationError(f"не вдалося розібрати «{value}». Приклади: {DURATION_EXAMPLES}")
        seconds = _unit_seconds(m.group(2))
        if seconds is None:
            raise DurationError(
                f"невідома одиниця «{m.group(2)}» у «{value}». Можна: д, год, хв, с (або d, h, m, s)"
            )
        total += float(m.group(1).replace(",", ".")) * seconds
        parts += 1
        pos = m.end()
    if parts == 0:
        raise DurationError(f"не вдалося розібрати «{value}». Приклади: {DURATION_EXAMPLES}")
    return int(round(total))


def format_duration(seconds: int) -> str:
    """Тривалість для людини: 86400 → «24 год», 5400 → «1 год 30 хв», 90 → «1 хв 30 с», 259200 → «3 д»."""
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400) if seconds >= 2 * 86400 else (0, seconds)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} год")
    if minutes:
        parts.append(f"{minutes} хв")
    if secs:
        parts.append(f"{secs} с")
    return " ".join(parts) or "0 с"


# ----------------------------------------------------------------------------- формат часу


def format_time_left(seconds_left: float, template: str = "auto") -> str:
    """Залишок часу для {time_left}. Хвилини округлюються вгору: на старті «24 год», в останні секунди «1 хв».

    template="auto" → «23 год 5 хв», «3 год», «45 хв»;
    інакше шаблон з {h} {hh} {m} {mm}, наприклад «{hh}:{mm}» → «03:05».
    """
    total_minutes = max(0, math.ceil(seconds_left / 60))
    hours, minutes = divmod(total_minutes, 60)
    if template == "auto":
        if hours and minutes:
            return f"{hours} год {minutes} хв"
        if hours:
            return f"{hours} год"
        return f"{minutes} хв"
    return fill(
        template,
        {"h": str(hours), "hh": f"{hours:02d}", "m": str(minutes), "mm": f"{minutes:02d}"},
    )


def format_countdown(seconds_left: float, template: str = "{hh}:{mm}:{ss}", step: int = 1) -> str:
    """Відлік для {countdown}: «23:59:55». Шаблон з {h} {hh} {m} {mm} {s} {ss}.

    step — крок оновлення таймера в секундах: значення округлюється вгору до кратного кроку від дедлайну
    (крок 5 с → «23:59:55», «23:59:50»…), тож відлік рівний і ніколи не показує менше, ніж є насправді.
    """
    step = max(1, int(step))
    left = max(0, math.ceil(seconds_left))
    shown = -(-left // step) * step  # ціле ділення з округленням угору
    hours, rest = divmod(shown, 3600)
    minutes, seconds = divmod(rest, 60)
    return fill(
        template,
        {
            "h": str(hours), "hh": f"{hours:02d}",
            "m": str(minutes), "mm": f"{minutes:02d}",
            "s": str(seconds), "ss": f"{seconds:02d}",
        },
    )


def format_progress(seconds_left: float, total: int, length: int = 10, full: str = "🟩", empty: str = "⬜️") -> str:
    """Смужка залишку часу для {progress}: «🟩🟩🟩🟩🟩🟩🟩⬜️⬜️⬜️» — заповнених клітинок стільки, скільки часу лишилось.

    Клітинка гасне, лише коли її частка часу минула повністю: остання тримається до самого кінця.
    """
    left = math.ceil(seconds_left)
    if left <= 0 or total <= 0:
        filled = 0
    else:
        filled = min(length, -(-left * length // total))  # цілочисельно: без похибок округлення
    return full * filled + empty * (length - filled)


def format_deadline(timestamp: int, tz: ZoneInfo, template: str) -> str:
    """Момент завершення для {deadline}: «{day} {month_name} о {time}» → «9 листопада о 14:35»."""
    dt = datetime.fromtimestamp(timestamp, tz)
    return fill(
        template,
        {
            "day": str(dt.day),
            "month": f"{dt.month:02d}",
            "month_name": MONTHS_GENITIVE[dt.month - 1],
            "year": str(dt.year),
            "time": f"{dt.hour:02d}:{dt.minute:02d}",
            "date": f"{dt.day:02d}.{dt.month:02d}",
        },
    )


# ----------------------------------------------------------------------------- тихі години

_SILENT_RE = re.compile(r"\s*(\d{1,2})[:.](\d{2})\s*[-–—]\s*(\d{1,2})[:.](\d{2})\s*")


@dataclass(frozen=True)
class SilentHours:
    """Інтервал місцевого часу, коли нагадування приходять без звуку. Може переходити через північ."""

    start: int  # хвилини від початку доби
    end: int

    def contains(self, timestamp: int, tz: ZoneInfo) -> bool:
        dt = datetime.fromtimestamp(timestamp, tz)
        minute = dt.hour * 60 + dt.minute
        if self.start == self.end:
            return False
        if self.start < self.end:
            return self.start <= minute < self.end
        return minute >= self.start or minute < self.end

    def describe(self) -> str:
        return f"{self.start // 60:02d}:{self.start % 60:02d}–{self.end // 60:02d}:{self.end % 60:02d}"


def parse_silent_hours(value: object) -> SilentHours | None:
    """«22:00-08:00» → SilentHours; порожнє значення або false → None (вимкнено)."""
    if value is None or value is False or (isinstance(value, str) and not value.strip()):
        return None
    m = _SILENT_RE.fullmatch(str(value))
    if m is None:
        raise DurationError("очікується інтервал у форматі «22:00-08:00» (або порожньо, щоб вимкнути)")
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if not (0 <= h1 <= 23 and 0 <= h2 <= 24 and 0 <= m1 <= 59 and 0 <= m2 <= 59) or (h2 == 24 and m2):
        raise DurationError("години мають бути від 00 до 23, хвилини — від 00 до 59")
    return SilentHours(h1 * 60 + m1, (h2 * 60 + m2) % 1440)
