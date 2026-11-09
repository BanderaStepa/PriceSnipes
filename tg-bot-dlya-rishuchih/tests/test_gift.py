"""Подарунок за швидке рішення: кнопка «🎁 Що за подарунок?», опис подарунка, /preview для адміністратора."""

import pytest

from bot.content import ContentError, parse_content
from bot.texts import CB_GIFT, Texts
from tests.fake_telegram import keyboard_of, tg_user
from tests.harness import DAY, T0

GIFT_TEXT = "🎁 <b>Курс «AI для реклами» у подарунок</b>\nВстигни, поки діє пропозиція: ще {time_left}"
ADMIN = 900


def with_gift(raw):
    raw["messages"]["gift"] = GIFT_TEXT
    raw["keyboards"]["offer"] = [["choose_package"], ["gift"]]
    raw["keyboards"]["reminder"] = [["choose_package"], ["gift"]]


@pytest.fixture
def gift_raw(raw_content):
    with_gift(raw_content)
    return raw_content


def test_gift_button_needs_gift_text(raw_content):
    raw_content["keyboards"]["offer"] = [["choose_package"], ["gift"]]
    with pytest.raises(ContentError, match="messages.gift: кнопка gift відкриває опис подарунка"):
        parse_content(raw_content)


def test_gift_button_is_not_allowed_after_expiry(gift_raw):
    gift_raw["keyboards"]["expired"] = [["gift"]]
    with pytest.raises(ContentError, match="кнопку gift тут використати не можна"):
        parse_content(gift_raw)


class Person:
    user_id = 1
    first_name = "Оля"
    username = None
    deadline_at = T0 + DAY


def test_gift_button_disappears_after_deadline(gift_raw):
    gift_raw["settings"]["packages_after_expiry"] = True  # навіть якщо продаж триває — подарунок згорів
    gift_raw["keyboards"]["expired"] = [["choose_package"]]
    texts = Texts(parse_content(gift_raw))
    before = keyboard_of(texts.keyboard("offer", Person(), T0))
    after = keyboard_of(texts.keyboard("offer", Person(), T0 + DAY))
    assert before == [[("Обрати пакет", "packages")], [("🎁 Що за подарунок?", CB_GIFT)]]
    assert after == [[("Обрати пакет", "packages")]]


async def test_gift_button_shows_gift_description(make):
    harness = await make(with_gift)
    await harness.send("/start")
    [offer] = harness.telegram.sent(1)
    assert keyboard_of(offer.reply_markup) == [[("Обрати пакет", "packages")], [("🎁 Що за подарунок?", "gift")]]
    harness.telegram.clear()

    await harness.press("gift")
    [gift] = harness.telegram.sent(1)
    assert gift.text == "🎁 <b>Курс «AI для реклами» у подарунок</b>\nВстигни, поки діє пропозиція: ще 24 год"
    assert keyboard_of(gift.reply_markup) == [[("Обрати пакет", "packages")]]


async def test_gift_after_deadline_says_offer_is_over(make):
    harness = await make(with_gift)
    await harness.send("/start")
    harness.clock.advance(DAY)
    harness.telegram.clear()
    await harness.press("gift")
    [reply] = harness.telegram.sent(1)
    assert reply.text.startswith("⌛️ Пропозиція для рішучих уже завершилась.")


async def test_reminders_carry_gift_button(make):
    harness = await make(with_gift)
    await harness.send("/start")
    harness.telegram.clear()
    harness.clock.t = T0 + 2 * 3600
    await harness.scheduler.tick()
    [reminder] = harness.telegram.sent(1)
    assert keyboard_of(reminder.reply_markup) == [[("Обрати пакет", "packages")], [("🎁 Що за подарунок?", "gift")]]


async def test_preview_for_admin_shows_everything(make):
    harness = await make(with_gift, admin_ids={ADMIN})
    await harness.send("/preview", tg_user(ADMIN, "Ігор"))
    messages = harness.telegram.sent(ADMIN)
    texts = [m.text for m in messages]
    assert texts[0].startswith("👀 <b>Попередній перегляд</b>")
    reminders = [t for t in texts if t.startswith("<i>Нагадування ")]
    assert [t.split()[1] for t in reminders] == ["r1", "r2", "r3", "r4"]
    assert "діє ще <b>22 год</b>" in reminders[0]  # залишок — як у момент нагадування
    assert "Ігор" in reminders[0]
    assert any("Кнопка «🎁» — опис подарунка" in t for t in texts)
    assert texts[-1].startswith("<i>Після завершення — на /start і будь-яку кнопку:</i>")
    assert await harness.storage.get_user(ADMIN) is None  # перегляд не запускає адміністратору таймер


async def test_preview_is_hidden_from_regular_users(make):
    harness = await make(with_gift, admin_ids={ADMIN})
    await harness.send("/start")
    harness.telegram.clear()
    await harness.send("/preview")
    [reply] = harness.telegram.sent(1)
    assert reply.text.startswith("Я автоматичний помічник")
