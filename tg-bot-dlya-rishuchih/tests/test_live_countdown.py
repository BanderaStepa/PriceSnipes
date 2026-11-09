"""Живий відлік: {countdown} із секундами і смужка {progress}; швидке оновлення, поки людина активна в чаті."""

import re

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import EditMessageText

from bot import sender as sender_module
from bot.content import ContentError, parse_content
from bot.scheduler import Scheduler
from bot.timeutils import format_countdown, format_progress
from tests.fake_telegram import keyboard_of, tg_user
from tests.harness import DAY, T0

MIN = 60
FULL, EMPTY = "🟩", "⬜️"


def live(raw):
    """Офер двома повідомленнями; у повідомленні з таймером — відлік і смужка; швидкий відлік 5 с протягом 10 хв."""
    raw["settings"].update(timer_fast_every="5 с", timer_fast_for="10 хв")
    raw["messages"]["offer"] = "Офер для {first_name}"
    raw["messages"]["offer_timer"] = "{timer}"
    raw["messages"]["timer"] = "⏳ <b>{countdown}</b>\n{progress}"


def live_without_fast_mode(raw):
    live(raw)
    raw["settings"].update(timer_fast_every=0, timer_fast_for=0)


async def at(harness, offset):
    """Перевести годинник на T0 + offset і оновити живі таймери."""
    harness.clock.t = T0 + offset
    await harness.scheduler.update_timers()


def countdowns(harness, chat_id=1):
    return [re.search(r"<b>([\d:]+)</b>", edit.text).group(1) for edit in harness.telegram.edits(chat_id)]


@pytest.fixture
def sleeps(monkeypatch):
    calls = []

    async def fake_sleep(seconds):
        calls.append(seconds)

    monkeypatch.setattr(sender_module, "_sleep", fake_sleep)
    return calls


# ----------------------------------------------------------------------------- формати


@pytest.mark.parametrize(
    ("seconds", "step", "expected"),
    [
        (DAY, 1, "24:00:00"),
        (DAY - 1, 1, "23:59:59"),
        (DAY - 1, 5, "24:00:00"),  # округлення вгору до кроку: ніколи не менше, ніж є насправді
        (DAY - 6, 5, "23:59:55"),
        (DAY - 61, 60, "23:59:00"),
        (3 * 3600 + 5 * 60 + 9, 1, "03:05:09"),
        (3 * DAY, 1, "72:00:00"),
        (0, 1, "00:00:00"),
        (-30, 5, "00:00:00"),
    ],
)
def test_countdown_format(seconds, step, expected):
    assert format_countdown(seconds, step=step) == expected


def test_countdown_custom_template():
    assert format_countdown(3 * 3600 + 5 * 60 + 9, "{h} год {mm} хв {ss} с") == "3 год 05 хв 09 с"


@pytest.mark.parametrize(
    ("left", "filled"),
    [(DAY, 10), (DAY - 1, 10), (DAY * 9 // 10, 9), (DAY * 9 // 10 - 1, 9), (DAY // 2, 5), (1, 1), (0, 0), (-5, 0)],
)
def test_progress_bar(left, filled):
    assert format_progress(left, DAY) == FULL * filled + EMPTY * (10 - filled)


def test_progress_bar_custom_symbols():
    assert format_progress(DAY // 2, DAY, 4, "▰", "▱") == "▰▰▱▱"


# ----------------------------------------------------------------------------- перевірка content.yaml


def test_fast_mode_is_off_by_default(raw_content):
    settings = parse_content(raw_content).settings
    assert (settings.timer_fast_every, settings.timer_fast_for) == (0, 0)
    assert settings.countdown_format == "{hh}:{mm}:{ss}"
    assert (settings.progress_length, settings.progress_full, settings.progress_empty) == (10, FULL, EMPTY)


def test_fast_mode_settings(raw_content):
    live(raw_content)
    settings = parse_content(raw_content).settings
    assert (settings.timer_fast_every, settings.timer_fast_for) == (5, 10 * MIN)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"timer_fast_every": "2 с", "timer_fast_for": "10 хв"}, "settings.timer_fast_every: щонайменше 3 с"),
        ({"timer_fast_every": "5 с"}, "вкажіть і timer_fast_every, і timer_fast_for"),
        ({"timer_fast_for": "10 хв"}, "вкажіть і timer_fast_every, і timer_fast_for"),
        ({"timer_fast_every": "2 хв", "timer_fast_for": "10 хв"}, "має бути частіше за timer_update_every"),
        ({"timer_fast_every": "5 с", "timer_fast_for": "10 хв", "timer_update_every": 0}, "швидкий відлік не працює"),
        ({"countdown_format": "відлік"}, "settings.countdown_format: шаблон має містити"),
        ({"progress_length": 0}, "settings.progress_length: ціле число від 1 до 20"),
        ({"progress_length": 21}, "settings.progress_length: ціле число від 1 до 20"),
        ({"progress_full": ""}, "settings.progress_full: текст порожній"),
    ],
)
def test_settings_errors(raw_content, change, error):
    raw_content["settings"].update(change)
    with pytest.raises(ContentError) as caught:
        parse_content(raw_content)
    assert any(error in message for message in caught.value.errors), caught.value.errors


def test_unknown_countdown_placeholder_is_a_warning(raw_content):
    raw_content["settings"]["countdown_format"] = "{hh}:{mm}:{sec}"
    warnings = parse_content(raw_content).warnings
    assert any(w.startswith("settings.countdown_format: невідома підстановка {sec}") for w in warnings)


def test_countdown_in_offer_body_does_not_tick(raw_content):
    live(raw_content)
    raw_content["messages"]["offer"] = "Офер {countdown}\n{progress}"
    warnings = parse_content(raw_content).warnings
    assert any(w.startswith("messages.offer: {countdown}, {progress} в офері не оновлюватиметься") for w in warnings)


# ----------------------------------------------------------------------------- живий таймер


async def test_countdown_ticks_every_5_seconds_while_person_is_in_chat(make):
    harness = await make(live)
    await harness.send("/start")
    body, timer = harness.telegram.sent(1)
    assert body.text == "Офер для Оля"
    assert timer.text == "⏳ <b>24:00:00</b>\n" + FULL * 10
    timer_id = (await harness.storage.get_user(1)).offer_message_id
    harness.telegram.clear()

    for second in range(1, 31):
        await at(harness, second)
    assert countdowns(harness) == ["23:59:55", "23:59:50", "23:59:45", "23:59:40", "23:59:35", "23:59:30"]
    edits = harness.telegram.edits(1)
    assert {edit.message_id for edit in edits} == {timer_id}  # редагується саме повідомлення з таймером
    assert keyboard_of(edits[-1].reply_markup) == [[("Обрати пакет", "packages")]]  # кнопка лишається


async def test_countdown_slows_down_when_person_leaves_and_speeds_up_on_any_action(make):
    harness = await make(live)
    await harness.send("/start")
    harness.telegram.clear()

    for second in range(1, 15 * MIN + 1):
        await at(harness, second)
    values = countdowns(harness)
    # 10 хв після /start — кожні 5 с (останнє — на 595-й секунді: 23:50:05), далі — раз на хвилину, цілими хвилинами
    assert len(values) == 119 + 5
    assert values[118] == "23:50:05"
    assert values[119:] == ["23:50:00", "23:49:00", "23:48:00", "23:47:00", "23:46:00"]
    assert values == sorted(values, reverse=True)  # відлік лише зменшується

    harness.clock.t = T0 + 15 * MIN + 10
    await harness.press("packages")  # будь-яка дія людини — знову швидкий відлік
    harness.telegram.clear()
    for second in range(15 * MIN + 11, 15 * MIN + 22):
        await at(harness, second)
    assert countdowns(harness) == ["23:44:50", "23:44:45", "23:44:40"]


async def test_without_fast_mode_timer_updates_once_a_minute(make):
    harness = await make(live_without_fast_mode)
    await harness.send("/start")
    _, timer = harness.telegram.sent(1)
    assert timer.text.startswith("⏳ <b>24:00:00</b>")
    harness.telegram.clear()
    for second in range(1, 3 * MIN + 1):
        await at(harness, second)
    assert countdowns(harness) == ["23:59:00", "23:58:00", "23:57:00"]


async def test_progress_bar_drains_over_the_day(make):
    harness = await make(live_without_fast_mode)
    await harness.send("/start")
    harness.telegram.clear()
    await at(harness, DAY // 2)
    [edit] = harness.telegram.edits(1)
    assert edit.text == "⏳ <b>12:00:00</b>\n" + FULL * 5 + EMPTY * 5
    harness.telegram.clear()
    await at(harness, DAY - 30)
    [edit] = harness.telegram.edits(1)
    assert edit.text == "⏳ <b>00:01:00</b>\n" + FULL + EMPTY * 9  # остання клітинка — до самого кінця


async def test_flood_control_pauses_only_that_chat_without_waiting(make, sleeps):
    harness = await make(live)
    await harness.send("/start", tg_user(1, "Оля"))
    harness.clock.t = T0 + 1
    await harness.send("/start", tg_user(2, "Ігор"))
    harness.telegram.clear()

    harness.telegram.fail_next(
        EditMessageText, lambda m: TelegramRetryAfter(method=m, message="Too Many Requests", retry_after=7)
    )
    await at(harness, 5)  # Оля: Telegram просить зачекати 7 с
    assert harness.funnel.timer_pause == {1: T0 + 12}
    assert sleeps == []  # бот не чекає — решта таймерів оновлюється далі
    harness.telegram.clear()

    for second in range(6, 12):
        await at(harness, second)
    assert countdowns(harness, 1) == []  # пауза для Олі
    assert countdowns(harness, 2) == ["23:59:55", "23:59:50"]  # Ігор — за розкладом

    await at(harness, 12)
    assert countdowns(harness, 1) == ["23:59:50"]
    assert harness.funnel.timer_pause == {}


async def test_timer_loop_runs_every_second_in_fast_mode(make):
    fast = await make(live)
    normal = await make(live_without_fast_mode)
    assert Scheduler(fast.funnel, tick_seconds=15).timer_tick_seconds == 1.0
    assert Scheduler(normal.funnel, tick_seconds=15).timer_tick_seconds == 15
