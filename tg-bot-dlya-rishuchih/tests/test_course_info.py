"""Кнопка «📖 Інформація про курс»: ще раз надіслати опис курсу (текст оферу), щоб не гортати чат угору."""

import pytest

from bot.content import ContentError, parse_content
from bot.texts import CB_COURSE
from tests.fake_telegram import keyboard_of
from tests.harness import DAY

COURSE = ("📖 Інформація про курс", CB_COURSE)
CHOOSE = ("Обрати пакет", "packages")
BODY = "<b>Курс «AI для сайтів»</b>\n<blockquote expandable>✅ Структура\n✅ Тексти</blockquote>"


def with_course_button(raw):
    """Офер двома повідомленнями (текст + таймер); кнопка course_info під таймером, під пакетом і в списку пакетів."""
    raw["messages"]["offer"] = BODY
    raw["messages"]["offer_timer"] = "⏳ {timer}"
    raw["keyboards"]["offer"] = [["choose_package"], ["course_info"]]
    raw["keyboards"]["package"] = [["get_payment_link"], ["other_packages"], ["course_info"]]
    raw["keyboards"]["packages_list"] = [["course_info"]]


async def started(harness):
    await harness.send("/start")
    harness.telegram.clear()


# ----------------------------------------------------------------------------- перевірка content.yaml


def test_button_is_off_unless_configured(raw_content):
    content = parse_content(raw_content)
    assert all("course_info" not in row for rows in content.keyboards.values() for row in rows)
    assert content.buttons["course_info"] == "📖 Інформація про курс"
    assert content.messages["course_info"] == ""  # порожньо — повторюється текст оферу


def test_course_info_button_is_not_allowed_under_itself(raw_content):
    raw_content["keyboards"]["course_info"] = [["course_info"]]
    with pytest.raises(ContentError, match="кнопку course_info тут використати не можна"):
        parse_content(raw_content)


def test_packages_list_takes_only_extra_buttons(raw_content):
    raw_content["keyboards"]["packages_list"] = [["choose_package"]]
    with pytest.raises(ContentError, match=r"keyboards\.packages_list\[1\]: кнопку choose_package тут використати не можна"):
        parse_content(raw_content)


# ----------------------------------------------------------------------------- сценарії


async def test_button_under_timer_resends_course_info(make):
    harness = await make(with_course_button)
    await harness.send("/start")
    body, timer = harness.telegram.sent(1)
    assert body.text == BODY
    assert keyboard_of(timer.reply_markup) == [[CHOOSE], [COURSE]]
    harness.telegram.clear()

    await harness.press(CB_COURSE)
    [again] = harness.telegram.sent(1)
    assert again.text == BODY  # той самий опис курсу, зі шторками
    assert keyboard_of(again.reply_markup) == [[CHOOSE]]  # keyboards.course_info за замовчуванням


async def test_button_under_package_and_in_packages_list(make):
    harness = await make(with_course_button)
    await started(harness)

    await harness.press("packages")
    [listing] = harness.telegram.sent(1)
    rows = keyboard_of(listing.reply_markup)
    assert len(rows) == len(harness.content.packages) + 1
    assert rows[-1] == [COURSE]  # під кнопками пакетів
    harness.telegram.clear()

    await harness.press("pkg:start")
    [description] = harness.telegram.sent(1)
    assert keyboard_of(description.reply_markup)[-1] == [COURSE]
    harness.telegram.clear()

    await harness.press(CB_COURSE)
    [again] = harness.telegram.sent(1)
    assert again.text == BODY


async def test_custom_course_info_text(make):
    def change(raw):
        with_course_button(raw)
        raw["messages"]["course_info"] = "📖 <b>Про курс</b> для {first_name}: пропозиція діє ще {time_left}"

    harness = await make(change)
    await started(harness)
    await harness.press(CB_COURSE)
    [reply] = harness.telegram.sent(1)
    assert reply.text == "📖 <b>Про курс</b> для Оля: пропозиція діє ще 24 год"


async def test_single_message_offer_is_repeated_with_current_time_left(make):
    # Еталонний контент: офер одним повідомленням із таймером усередині — у повторі залишок на момент натискання
    harness = await make(lambda raw: raw["keyboards"].update(offer=[["choose_package"], ["course_info"]]))
    await started(harness)
    harness.clock.advance(3600)
    await harness.press(CB_COURSE)
    [reply] = harness.telegram.sent(1)
    assert reply.text.startswith("Привіт, Оля! 👋")
    assert "Залишилось: <b>23 год</b>" in reply.text


async def test_after_deadline_button_still_shows_course_info(make):
    # Опис курсу — не акція: і після завершення пропозиції кнопка показує його, але вже без кнопок пакетів
    harness = await make(with_course_button)
    await started(harness)
    harness.clock.advance(DAY)
    await harness.press(CB_COURSE)
    [reply] = harness.telegram.sent(1)
    assert reply.text == BODY
    assert reply.reply_markup is None


async def test_press_is_recorded_for_statistics(make):
    harness = await make(with_course_button)
    await started(harness)
    await harness.press(CB_COURSE)
    await harness.press(CB_COURSE)
    async with harness.storage._conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'course_info'") as cursor:
        assert (await cursor.fetchone())[0] == 2
