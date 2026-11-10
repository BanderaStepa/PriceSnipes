"""Після завершення пропозиції: порожній messages.expired — бот надсилає опис курсу замість «пропозиція завершилась»."""

from bot.content import parse_content
from bot.texts import CB_COURSE
from tests.fake_telegram import keyboard_of
from tests.harness import DAY, T0

COURSE = ("📖 Інформація про курс", CB_COURSE)
CHOOSE = ("Обрати пакет", "packages")
BODY = "<b>Курс «AI для сайтів»</b>\n<blockquote expandable>✅ Структура\n✅ Тексти</blockquote>"


def course_info_after_expiry(raw):
    """Як у замовника: офер (опис курсу) і таймер окремими повідомленнями, після дедлайну — опис курсу."""
    raw["messages"]["offer"] = BODY
    raw["messages"]["offer_timer"] = "⏳ {timer}"
    raw["messages"]["expired"] = ""
    raw["keyboards"]["offer"] = [["choose_package"], ["course_info"]]


async def expired_harness(make, change=course_info_after_expiry):
    harness = await make(change)
    await harness.send("/start")
    harness.clock.t = T0 + DAY
    harness.telegram.clear()
    return harness


def test_empty_expired_is_allowed(raw_content):
    course_info_after_expiry(raw_content)
    assert parse_content(raw_content).messages["expired"] == ""


async def test_start_after_deadline_sends_course_info_without_timer(make):
    harness = await expired_harness(make)
    await harness.send("/start")
    [reply] = harness.telegram.sent(1)  # одне повідомлення: опис курсу, без нового таймера
    assert reply.text == BODY
    assert reply.reply_markup is None  # keyboards.expired порожня — під описом нічого


async def test_any_button_or_message_after_deadline_gets_course_info(make):
    harness = await expired_harness(make)
    for action in ("packages", "pkg:start", "pay:start", "gift", CB_COURSE):
        await harness.press(action)  # старі кнопки з повідомлень, надісланих до дедлайну
    await harness.send("Скільки коштує курс?")
    replies = harness.telegram.sent(1)
    assert [r.text for r in replies] == [BODY] * 6
    assert all(r.reply_markup is None for r in replies)


async def test_timer_keeps_only_course_info_button_after_deadline(make):
    harness = await make(course_info_after_expiry)
    await harness.send("/start")
    _, timer = harness.telegram.sent(1)
    assert keyboard_of(timer.reply_markup) == [[CHOOSE], [COURSE]]
    harness.telegram.clear()

    harness.clock.t = T0 + DAY
    await harness.scheduler.tick()
    [edit] = harness.telegram.edits(1)
    assert edit.text == "⏳ ⌛️ Час вийшов — пропозиція завершилась."
    assert keyboard_of(edit.reply_markup) == [[COURSE]]  # «Обрати пакет» прибрано, опис курсу — лишився
    assert harness.telegram.sent() == []

    await harness.press(CB_COURSE)
    [reply] = harness.telegram.sent(1)
    assert reply.text == BODY


async def test_custom_expired_text_is_still_used(make):
    def change(raw):
        course_info_after_expiry(raw)
        raw["messages"]["expired"] = "⌛️ {first_name}, пропозиція завершилась."

    harness = await expired_harness(make, change)
    await harness.send("/start")
    [reply] = harness.telegram.sent(1)
    assert reply.text == "⌛️ Оля, пропозиція завершилась."
