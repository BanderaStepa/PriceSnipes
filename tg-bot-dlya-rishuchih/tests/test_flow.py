"""Сценарії користувача через справжній диспетчер aiogram і фейковий Telegram."""

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, SendMessage

from tests.fake_telegram import keyboard_of, tg_user
from tests.harness import DAY, T0

PACKAGE_BUTTONS = [
    [("Старт — 1 990 грн", "pkg:start")],
    [("Оптимальний — 3 990 грн", "pkg:optimal")],
    [("VIP — 7 990 грн", "pkg:vip")],
]
MANAGER = -100500


async def test_start_sends_offer_with_timer_and_button(harness):
    await harness.send("/start")
    [offer] = harness.telegram.sent(1)
    assert offer.parse_mode == "HTML"
    assert offer.text.startswith("Привіт, Оля! 👋")
    assert "<b>Пропозиція для рішучих. Діє 24 години</b>" in offer.text
    assert "⏳ Залишилось: <b>24 год</b> (до 9 листопада о 12:00)" in offer.text
    assert keyboard_of(offer.reply_markup) == [[("Обрати пакет", "packages")]]

    user = await harness.storage.get_user(1)
    assert (user.started_at, user.deadline_at) == (T0, T0 + DAY)
    assert user.offer_message_id is not None


async def test_repeated_start_keeps_timer_and_replaces_offer(harness):
    await harness.send("/start")
    first_offer = (await harness.storage.get_user(1)).offer_message_id
    harness.clock.advance(5 * 3600)
    harness.telegram.clear()

    await harness.send("/start")
    [offer] = harness.telegram.sent(1)
    assert "⏳ Залишилось: <b>19 год</b> (до 9 листопада о 12:00)" in offer.text  # таймер не перезапустився
    user = await harness.storage.get_user(1)
    assert (user.started_at, user.deadline_at) == (T0, T0 + DAY)
    assert user.offer_message_id not in (None, first_offer)
    assert [d.message_id for d in harness.telegram.deleted()] == [first_offer]  # старий офер прибрано


async def test_deep_link_source_is_saved(harness):
    await harness.send("/start instagram")
    assert (await harness.storage.get_user(1)).source == "instagram"


async def test_choose_package_lists_names_and_prices(harness):
    await harness.send("/start")
    harness.telegram.clear()
    await harness.press("packages")
    assert len(harness.telegram.calls(AnswerCallbackQuery)) == 1
    [message] = harness.telegram.sent(1)
    assert message.text == "Оберіть пакет 👇"
    assert keyboard_of(message.reply_markup) == PACKAGE_BUTTONS


async def test_package_description_with_navigation_buttons(harness):
    await harness.send("/start")
    harness.telegram.clear()
    await harness.press("pkg:vip")
    [message] = harness.telegram.sent(1)
    assert message.text.startswith("<b>Пакет «VIP» — 7 990 грн</b>")
    assert "Особисті консультації" in message.text
    assert keyboard_of(message.reply_markup) == [
        [("Отримати посилання на оплату", "pay:vip")],
        [("Подивитися інші пакети", "packages")],
    ]

    harness.telegram.clear()
    await harness.press("packages")  # «Подивитися інші пакети»
    [message] = harness.telegram.sent(1)
    assert keyboard_of(message.reply_markup) == PACKAGE_BUTTONS


async def test_payment_link_for_the_chosen_package(harness):
    await harness.send("/start")
    harness.telegram.clear()
    await harness.press("pay:optimal")
    [message] = harness.telegram.sent()  # менеджер не налаштований — лише одне повідомлення
    assert message.chat_id == 1
    assert "<b>Оплата пакета «Оптимальний»</b> — 3 990 грн" in message.text
    assert "https://example.com/pay/optimal" in message.text
    assert keyboard_of(message.reply_markup) == [
        [("💳 Перейти до оплати", "https://example.com/pay/optimal")],
        [("Подивитися інші пакети", "packages")],
    ]
    stats = await harness.storage.stats(harness.clock.now())
    assert stats.payment_links == {"optimal": (1, 1)}


async def test_manager_is_notified_about_payment_link(make):
    harness = await make(manager_chat_id=MANAGER)
    await harness.send("/start", tg_user(5, "Іван", "ivan"))
    harness.telegram.clear()
    await harness.press("pay:start", tg_user(5, "Іван", "ivan"))
    [to_user] = harness.telegram.sent(5)
    [to_manager] = harness.telegram.sent(MANAGER)
    assert "https://example.com/pay/start" in to_user.text
    assert "Пакет: Старт — 1 990 грн" in to_manager.text
    assert '<a href="tg://user?id=5">Іван</a> @ivan' in to_manager.text


async def test_unreachable_manager_does_not_affect_user(make):
    harness = await make(manager_chat_id=MANAGER)
    harness.telegram.blocked.add(MANAGER)
    await harness.send("/start")
    await harness.press("pay:start")
    assert any("https://example.com/pay/start" in m.text for m in harness.telegram.sent(1))
    assert not (await harness.storage.get_user(1)).is_blocked


async def test_button_of_removed_package(harness):
    await harness.send("/start")
    harness.telegram.clear()
    await harness.press("pkg:old-package")
    [answer] = harness.telegram.calls(AnswerCallbackQuery)
    assert answer.text == "Цей пакет більше недоступний. Оберіть інший 👇"
    [message] = harness.telegram.sent(1)
    assert keyboard_of(message.reply_markup) == PACKAGE_BUTTONS


async def test_after_deadline_offer_is_no_longer_valid(harness):
    await harness.send("/start")
    harness.clock.advance(DAY)
    harness.telegram.clear()

    for action in (harness.press("packages"), harness.press("pkg:vip"), harness.press("pay:vip"),
                   harness.send("/start"), harness.send("Привіт!")):
        await action
    messages = harness.telegram.sent(1)
    assert len(messages) == 5
    for message in messages:
        assert message.text.startswith("⌛️ Пропозиція для рішучих уже завершилась.")
        assert message.reply_markup is None
    assert (await harness.storage.get_user(1)).deadline_at == T0 + DAY  # /start не перезапустив таймер


async def test_packages_can_stay_available_after_deadline(make):
    def change(raw):
        raw["settings"]["packages_after_expiry"] = True
        raw["keyboards"]["expired"] = [["choose_package"]]
        raw["packages"][0]["price_after_expiry"] = "2 990 грн"
        raw["packages"][0]["payment_url_after_expiry"] = "https://pay.example.org/start-full"

    harness = await make(change)
    await harness.send("/start")
    harness.clock.advance(DAY + 60)
    harness.telegram.clear()

    await harness.send("/start")
    [expired] = harness.telegram.sent(1)
    assert keyboard_of(expired.reply_markup) == [[("Обрати пакет", "packages")]]
    harness.telegram.clear()
    await harness.press("pay:start")
    [payment] = harness.telegram.sent(1)
    assert "2 990 грн" in payment.text
    assert keyboard_of(payment.reply_markup)[0] == [("💳 Перейти до оплати", "https://pay.example.org/start-full")]


async def test_free_text_gets_hint_with_button(harness):
    await harness.send("/start")
    harness.telegram.clear()
    await harness.send("А є розстрочка?")
    [reply] = harness.telegram.sent(1)
    assert reply.text.startswith("Я автоматичний помічник")
    assert keyboard_of(reply.reply_markup) == [[("Обрати пакет", "packages")]]


async def test_empty_fallback_means_no_reply(make):
    harness = await make(lambda raw: raw["messages"].update(fallback=""))
    await harness.send("/start")
    harness.telegram.clear()
    await harness.send("Привіт")
    assert harness.telegram.sent() == []


async def test_group_chats_are_ignored(harness):
    await harness.send("/start", chat_type="group", chat_id=-42)
    await harness.send("Привіт", chat_type="supergroup", chat_id=-42)
    assert harness.telegram.sent() == []
    assert await harness.storage.get_user(1) is None


async def test_button_without_start_begins_the_funnel(harness):
    # наприклад, база була очищена, а в чаті лишилась стара кнопка
    await harness.press("pay:vip")
    [offer] = harness.telegram.sent(1)
    assert "Пропозиція для рішучих" in offer.text
    assert (await harness.storage.get_user(1)).deadline_at == T0 + DAY


async def test_profile_is_updated_and_names_escaped(harness):
    await harness.send("/start", tg_user(1, "Оля"))
    harness.telegram.clear()
    await harness.send("/start", tg_user(1, "<Ольга> & Ко", None))
    [offer] = harness.telegram.sent(1)
    assert offer.text.startswith("Привіт, &lt;Ольга&gt; &amp; Ко! 👋")
    user = await harness.storage.get_user(1)
    assert (user.first_name, user.username) == ("<Ольга> & Ко", None)


async def test_broken_markup_is_sent_as_plain_text(harness):
    harness.telegram.fail_next(
        SendMessage, lambda m: TelegramBadRequest(method=m, message="Bad Request: can't parse entities: unsupported")
    )
    await harness.send("/start")
    first, second = harness.telegram.sent(1)
    assert first.parse_mode == "HTML" and second.parse_mode is None
    assert "<b>" not in second.text and "Пропозиція для рішучих. Діє 24 години" in second.text
    assert (await harness.storage.get_user(1)).offer_message_id is not None


async def test_two_users_have_independent_timers(harness):
    await harness.send("/start", tg_user(1, "Оля"))
    harness.clock.advance(3 * 3600)
    await harness.send("/start", tg_user(2, "Петро"))
    first, second = await harness.storage.get_user(1), await harness.storage.get_user(2)
    assert first.deadline_at == T0 + DAY
    assert second.deadline_at == T0 + 3 * 3600 + DAY
