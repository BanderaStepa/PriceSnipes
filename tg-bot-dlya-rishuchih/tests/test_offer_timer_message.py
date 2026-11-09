"""Офер двома повідомленнями: текст (зі «шторками», не редагується) + коротке повідомлення з живим таймером і кнопкою."""

import sqlite3

from bot.content import parse_content
from bot.htmltext import prepare_html
from bot.storage import _MIGRATIONS, SCHEMA_VERSION, Storage
from tests.fake_telegram import keyboard_of
from tests.harness import DAY, T0, make_harness

BODY = (
    "<b>AI для створення сайтів</b>\n\n"
    "🎁 <b>Що ви отримаєте?</b>\n"
    "<blockquote expandable>✅ Структуру сайту\n✅ Тексти, які продають\n✅ План запуску\n✅ Аналітику</blockquote>"
)
TIMER = "🔥 <b>Пропозиція для рішучих. Діє 24 години</b>\n{timer}"


def separate(raw):
    raw["messages"]["offer"] = BODY
    raw["messages"]["offer_timer"] = TIMER


async def started(harness):
    await harness.send("/start")
    body, timer = harness.telegram.sent(1)
    harness.telegram.clear()
    return body, timer


def test_expandable_quote_and_spoiler_are_valid_markup():
    checked = prepare_html('<blockquote expandable>Шторка</blockquote> <tg-spoiler>спойлер</tg-spoiler>')
    assert checked.errors == []


def test_offer_timer_warnings(raw_content):
    raw_content["messages"]["offer_timer"] = "Без таймера"  # а {timer} лишився в offer
    warnings = parse_content(raw_content).warnings
    assert any(w.startswith("messages.offer_timer: немає підстановки {timer}") for w in warnings)
    assert any(w.startswith("messages.offer: таймер показується окремим повідомленням") for w in warnings)


async def test_start_sends_offer_text_then_timer_with_button(make):
    harness = await make(separate)
    await harness.send("/start")
    body, timer = harness.telegram.sent(1)
    assert body.text == BODY  # текст оферу — як у content.yaml
    assert body.reply_markup is None
    assert timer.text == "🔥 <b>Пропозиція для рішучих. Діє 24 години</b>\n⏳ Залишилось: <b>24 год</b> (до 9 листопада о 12:00)"
    assert keyboard_of(timer.reply_markup) == [[("Обрати пакет", "packages")]]
    user = await harness.storage.get_user(1)
    assert user.offer_message_id is not None and user.offer_body_message_id is not None
    assert user.offer_message_id != user.offer_body_message_id


async def test_only_timer_message_is_edited(make):
    harness = await make(separate)
    await started(harness)
    user = await harness.storage.get_user(1)
    harness.clock.t = T0 + 61
    await harness.scheduler.tick()
    [edit] = harness.telegram.edits(1)
    assert edit.message_id == user.offer_message_id  # текст оферу (зі шторками) не чіпаємо
    assert edit.text == "🔥 <b>Пропозиція для рішучих. Діє 24 години</b>\n⏳ Залишилось: <b>23 год 59 хв</b> (до 9 листопада о 12:00)"

    harness.telegram.clear()
    harness.clock.t = T0 + DAY
    await harness.scheduler.tick()
    [final] = harness.telegram.edits(1)
    assert final.message_id == user.offer_message_id
    assert final.text.endswith("⌛️ Час вийшов — пропозиція завершилась.")
    assert final.reply_markup is None


async def test_repeated_start_replaces_both_messages(make):
    harness = await make(separate)
    await started(harness)
    first = await harness.storage.get_user(1)
    harness.clock.advance(3600)
    await harness.send("/start")
    assert len(harness.telegram.sent(1)) == 2
    assert sorted(d.message_id for d in harness.telegram.deleted()) == sorted(
        [first.offer_message_id, first.offer_body_message_id]
    )
    second = await harness.storage.get_user(1)
    assert second.deadline_at == first.deadline_at  # таймер не перезапустився


async def test_switch_to_separate_timer_keeps_old_single_offer_intact(tmp_path, raw_content):
    """Офер надіслано одним повідомленням, потім content.yaml перейшов на окремий таймер."""
    db_path = tmp_path / "bot.db"
    first = await make_harness(parse_content(raw_content), db_path)
    await first.send("/start")
    single_id = (await first.storage.get_user(1)).offer_message_id
    await first.close()

    separate(raw_content)
    second = await make_harness(parse_content(raw_content), db_path, start=T0 + 61)
    try:
        await second.scheduler.tick()
        [edit] = second.telegram.edits(1)
        assert edit.message_id == single_id
        assert edit.text.startswith("<b>AI для створення сайтів</b>")  # текст оферу на місці
        assert edit.text.endswith("⏳ Залишилось: <b>23 год 59 хв</b> (до 9 листопада о 12:00)")  # і таймер теж
    finally:
        await second.close()


async def test_database_from_previous_version_is_migrated(tmp_path):
    path = tmp_path / "bot.db"
    with sqlite3.connect(path) as conn:  # база, створена попередньою версією бота (схема 1)
        conn.executescript(_MIGRATIONS[1] + "PRAGMA user_version = 1;")
        conn.execute(
            "INSERT INTO users (user_id, chat_id, first_name, started_at, deadline_at, offer_message_id, last_seen_at)"
            " VALUES (1, 1, 'Оля', ?, ?, 555, ?)",
            (T0, T0 + DAY, T0),
        )
    storage = await Storage.open(path)
    try:
        user = await storage.get_user(1)
        assert (user.started_at, user.deadline_at, user.offer_message_id) == (T0, T0 + DAY, 555)
        assert user.offer_body_message_id is None
        await storage.set_offer_message(1, 600, 599)
        assert (await storage.get_user(1)).offer_body_message_id == 599
    finally:
        await storage.close()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 2
