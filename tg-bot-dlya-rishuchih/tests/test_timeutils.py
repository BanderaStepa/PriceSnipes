from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bot.timeutils import (
    DurationError,
    SilentHours,
    format_deadline,
    format_duration,
    format_time_left,
    parse_duration,
    parse_silent_hours,
)

KYIV = ZoneInfo("Europe/Kyiv")


def ts(*args: int) -> int:
    """Київський місцевий час → unix-секунди."""
    return int(datetime(*args, tzinfo=KYIV).timestamp())


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("24 год", 86400),
        ("24год", 86400),
        ("24 години", 86400),
        ("1 година", 3600),
        ("90 хв", 5400),
        ("30 хвилин", 1800),
        ("1 год 30 хв", 5400),
        ("1 год, 30 хв", 5400),
        ("1,5 год", 5400),
        ("1.5h", 5400),
        ("1h30m", 5400),
        ("2 д", 172800),
        ("1 доба", 86400),
        ("45 с", 45),
        ("10 секунд", 10),
        ("2 Год", 7200),
        ("0", 0),
        (0, 0),
    ],
)
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("bad", ["", "24", 24, 1.5, "abc", "5 тижнів", "год", True, None, ["1 год"]])
def test_parse_duration_rejects(bad):
    with pytest.raises(DurationError):
        parse_duration(bad)


def test_parse_duration_error_mentions_units():
    with pytest.raises(DurationError, match="одиниці"):
        parse_duration(24)


def test_format_duration():
    assert format_duration(86400) == "24 год"
    assert format_duration(2 * 86400 + 3600) == "2 д 1 год"
    assert format_duration(5400) == "1 год 30 хв"
    assert format_duration(90) == "1 хв 30 с"
    assert format_duration(0) == "0 с"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (86400, "24 год"),
        (86399, "24 год"),  # хвилини округлюються вгору
        (86340, "23 год 59 хв"),
        (3 * 3600, "3 год"),
        (3 * 3600 + 1, "3 год 1 хв"),
        (45 * 60, "45 хв"),
        (30, "1 хв"),
        (0, "0 хв"),
        (-50, "0 хв"),
    ],
)
def test_format_time_left_auto(seconds, expected):
    assert format_time_left(seconds) == expected


def test_format_time_left_template():
    assert format_time_left(3 * 3600 + 5 * 60, "{hh}:{mm}") == "03:05"
    assert format_time_left(3 * 3600 + 5 * 60, "{h} год {mm} хв") == "3 год 05 хв"
    assert format_time_left(59, "{m} хв {невідома}") == "1 хв {невідома}"


def test_format_deadline_in_kyiv_time():
    moment = ts(2026, 11, 9, 14, 35, 59)
    assert format_deadline(moment, KYIV, "{day} {month_name} о {time}") == "9 листопада о 14:35"
    assert format_deadline(moment, KYIV, "{date}.{year}, {time}") == "09.11.2026, 14:35"
    assert format_deadline(moment, KYIV, "{month}") == "11"


def test_format_deadline_respects_summer_time():
    # 1 липня Київ — UTC+3, 1 грудня — UTC+2
    summer = int(datetime(2026, 7, 1, 9, 0, tzinfo=ZoneInfo("UTC")).timestamp())
    winter = int(datetime(2026, 12, 1, 9, 0, tzinfo=ZoneInfo("UTC")).timestamp())
    assert format_deadline(summer, KYIV, "{time}") == "12:00"
    assert format_deadline(winter, KYIV, "{time}") == "11:00"


def test_silent_hours_across_midnight():
    silent = parse_silent_hours("22:00-08:00")
    assert silent == SilentHours(22 * 60, 8 * 60)
    assert silent.contains(ts(2026, 11, 8, 23, 30), KYIV)
    assert silent.contains(ts(2026, 11, 9, 7, 59), KYIV)
    assert not silent.contains(ts(2026, 11, 9, 8, 0), KYIV)
    assert not silent.contains(ts(2026, 11, 9, 21, 59), KYIV)
    assert silent.describe() == "22:00–08:00"


def test_silent_hours_same_day():
    silent = parse_silent_hours("13:00 – 14:30")
    assert silent.contains(ts(2026, 11, 9, 13, 0), KYIV)
    assert not silent.contains(ts(2026, 11, 9, 14, 30), KYIV)


@pytest.mark.parametrize("off", ["", "  ", None, False])
def test_silent_hours_disabled(off):
    assert parse_silent_hours(off) is None


@pytest.mark.parametrize("bad", ["22-08", "25:00-08:00", "22:61-08:00", "вночі"])
def test_silent_hours_rejects(bad):
    with pytest.raises(DurationError):
        parse_silent_hours(bad)
