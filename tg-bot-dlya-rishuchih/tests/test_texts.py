from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bot.content import parse_content
from bot.texts import CB_PACKAGES, Texts

KYIV = ZoneInfo("Europe/Kyiv")
START = int(datetime(2026, 11, 8, 14, 35, 10, tzinfo=KYIV).timestamp())
DEADLINE = START + 86400


@dataclass
class User:
    user_id: int = 42
    first_name: str = "Оля"
    username: str | None = "olya"
    deadline_at: int = DEADLINE


@pytest.fixture
def texts(raw_content):
    return Texts(parse_content(raw_content))


def buttons(markup):
    """Клавіатура → [[(текст, callback або url), …], …] для зручного порівняння."""
    if markup is None:
        return None
    return [[(b.text, b.callback_data or b.url) for b in row] for row in markup.inline_keyboard]


def test_offer_at_start(texts):
    text = texts.offer(User(), START)
    assert text.startswith("Привіт, Оля! 👋")
    assert "<b>Пропозиція для рішучих. Діє 24 години</b>" in text
    assert text.endswith("⏳ Залишилось: <b>24 год</b> (до 9 листопада о 14:35)")


def test_offer_timer_counts_down(texts):
    text = texts.offer(User(), START + 3600 + 25 * 60)
    assert "⏳ Залишилось: <b>22 год 35 хв</b> (до 9 листопада о 14:35)" in text


def test_offer_after_deadline_shows_expired_timer(texts):
    text = texts.offer(User(), DEADLINE)
    assert text.endswith("⌛️ Час вийшов — пропозиція завершилась.")
    assert "Залишилось" not in text


def test_user_name_is_escaped(texts):
    text = texts.offer(User(first_name="<b>Hacker</b> & Co"), START)
    assert "Привіт, &lt;b&gt;Hacker&lt;/b&gt; &amp; Co!" in text


def test_offer_keyboard_until_deadline(texts):
    assert buttons(texts.keyboard("offer", User(), START)) == [[("Обрати пакет", CB_PACKAGES)]]
    assert buttons(texts.keyboard("offer", User(), DEADLINE - 1)) == [[("Обрати пакет", CB_PACKAGES)]]
    assert texts.keyboard("offer", User(), DEADLINE) is None


def test_packages_list_and_keyboard(texts):
    assert texts.packages_list(User(), START) == "Оберіть пакет 👇"
    assert buttons(texts.packages_keyboard(User(), START)) == [
        [("Старт — 1 990 грн", "pkg:start")],
        [("Оптимальний — 3 990 грн", "pkg:optimal")],
        [("VIP — 7 990 грн", "pkg:vip")],
    ]


def test_packages_list_placeholder_and_row_layout(raw_content):
    raw_content["messages"]["packages_list"] = "Пакети:\n{packages}"
    raw_content["settings"]["packages_per_row"] = 2
    texts = Texts(parse_content(raw_content))
    assert texts.packages_list(User(), START) == (
        "Пакети:\n1. <b>Старт</b> — 1 990 грн\n2. <b>Оптимальний</b> — 3 990 грн\n3. <b>VIP</b> — 7 990 грн"
    )
    assert [len(row) for row in texts.packages_keyboard(User(), START).inline_keyboard] == [2, 1]


def test_package_description_and_buttons_in_configured_order(texts):
    vip = texts.content.package("vip")
    description = texts.package_description(vip, User(), START)
    assert description.startswith("<b>Пакет «VIP» — 7 990 грн</b>")
    assert buttons(texts.keyboard("package", User(), START, vip)) == [
        [("Отримати посилання на оплату", "pay:vip")],
        [("Подивитися інші пакети", CB_PACKAGES)],
    ]


def test_payment_message_and_link_button(texts):
    start = texts.content.package("start")
    text = texts.payment(start, User(), START)
    assert "<b>Оплата пакета «Старт»</b> — 1 990 грн" in text
    assert "https://example.com/pay/start" in text
    assert buttons(texts.keyboard("payment", User(), START, start)) == [
        [("💳 Перейти до оплати", "https://example.com/pay/start")],
        [("Подивитися інші пакети", CB_PACKAGES)],
    ]


def test_after_expiry_packages_can_stay_open_with_regular_price(raw_content):
    raw_content["settings"]["packages_after_expiry"] = True
    raw_content["packages"][0]["price_after_expiry"] = "2 990 грн"
    raw_content["packages"][0]["payment_url_after_expiry"] = "https://pay.example.org/start-full"
    texts = Texts(parse_content(raw_content))
    start = texts.content.package("start")
    assert texts.packages_open(User(), DEADLINE)
    assert "2 990 грн" in texts.payment(start, User(), DEADLINE)
    assert buttons(texts.keyboard("payment", User(), DEADLINE, start))[0] == [
        ("💳 Перейти до оплати", "https://pay.example.org/start-full")
    ]
    assert buttons(texts.packages_keyboard(User(), DEADLINE))[0] == [("Старт — 2 990 грн", "pkg:start")]
    # до дедлайну — звичайна ціна пропозиції
    assert "1 990 грн" in texts.payment(start, User(), START)


def test_reminder_shows_time_left_at_send_moment(texts):
    r3 = next(r for r in texts.content.reminders if r.id == "r3")
    text = texts.reminder(r3, User(), DEADLINE - 3 * 3600)
    assert "Залишилось лише <b>3 год</b>" in text
    r4 = next(r for r in texts.content.reminders if r.id == "r4")
    assert "діє до 9 листопада о 14:35" in texts.reminder(r4, User(), DEADLINE - 3600)


def test_expired_and_fallback_texts(texts):
    assert texts.expired(User(), DEADLINE).startswith("⌛️ Пропозиція для рішучих уже завершилась.")
    assert texts.expired_notice(User(), DEADLINE).startswith("⌛️ Оля, пропозиція для рішучих завершилась.")
    assert texts.fallback(User(), START).startswith("Я автоматичний помічник")
    assert texts.package_unavailable() == "Цей пакет більше недоступний. Оберіть інший 👇"


def test_manager_notification(texts):
    optimal = texts.content.package("optimal")
    text = texts.manager_payment_request(optimal, User(first_name="Оля & Ко"), START + 3600)
    assert "Пакет: Оптимальний — 3 990 грн" in text
    assert '<a href="tg://user?id=42">Оля &amp; Ко</a> @olya' in text
    assert "ID: <code>42</code>" in text
    assert "До кінця пропозиції: 23 год" in text
    without_username = texts.manager_payment_request(optimal, User(username=None), START)
    assert '<a href="tg://user?id=42">Оля</a>\nID:' in without_username  # без зайвого пробілу в кінці рядка


def test_custom_time_format(raw_content):
    raw_content["settings"]["time_left_format"] = "{hh}:{mm}"
    raw_content["settings"]["deadline_format"] = "{date} о {time}"
    texts = Texts(parse_content(raw_content))
    assert texts.timer_line(User(), START + 65) == "⏳ Залишилось: <b>23:59</b> (до 09.11 о 14:35)"


def test_informational_package_has_no_payment_buttons(raw_content):
    for package in raw_content["packages"]:
        del package["payment_url"]
    texts = Texts(parse_content(raw_content))
    vip = texts.content.package("vip")
    assert buttons(texts.keyboard("package", User(), START, vip)) == [[("Подивитися інші пакети", CB_PACKAGES)]]
    assert buttons(texts.keyboard("payment", User(), START, vip)) == [[("Подивитися інші пакети", CB_PACKAGES)]]
