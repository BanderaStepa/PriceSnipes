import sqlite3

import pytest

from bot.storage import (
    EVENT_PACKAGE,
    EVENT_PAYMENT_LINK,
    REMINDER_SENT,
    REMINDER_SKIPPED,
    SCHEMA_VERSION,
    Profile,
    Storage,
)

DAY = 86400
T0 = 1_800_000_000


@pytest.fixture
async def db(tmp_path):
    storage = await Storage.open(tmp_path / "data" / "bot.db")
    yield storage
    await storage.close()


def olya(**changes) -> Profile:
    values = dict(user_id=1, chat_id=1, first_name="Оля", username="olya", language_code="uk")
    values.update(changes)
    return Profile(**values)


async def test_creates_file_and_schema(tmp_path):
    path = tmp_path / "nested" / "bot.db"
    storage = await Storage.open(path)
    await storage.close()
    assert path.exists()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


async def test_first_start_creates_timer(db):
    user, created = await db.register_start(olya(), now=T0, duration=DAY, source="instagram")
    assert created
    assert (user.started_at, user.deadline_at) == (T0, T0 + DAY)
    assert user.first_name == "Оля" and user.source == "instagram"
    assert not user.is_blocked and not user.expiry_done and user.offer_message_id is None


async def test_repeated_start_never_restarts_timer(db):
    await db.register_start(olya(), now=T0, duration=DAY, source="instagram")
    await db.mark_blocked(1, now=T0 + 10)
    user, created = await db.register_start(
        olya(first_name="Ольга", username=None), now=T0 + 5 * 3600, duration=2 * DAY, source="tiktok"
    )
    assert not created
    assert (user.started_at, user.deadline_at) == (T0, T0 + DAY)  # таймер не змінився
    assert user.source == "instagram"  # джерело — лише з першого запуску
    assert user.first_name == "Ольга" and user.username is None  # профіль оновлено
    assert not user.is_blocked  # повернувся — позначку знято
    assert user.last_seen_at == T0 + 5 * 3600


async def test_offer_message_id(db):
    await db.register_start(olya(), now=T0, duration=DAY)
    await db.set_offer_message(1, 100)
    await db.clear_offer_message(1, 99)  # інше (старіше) повідомлення — не чіпаємо
    assert (await db.get_user(1)).offer_message_id == 100
    await db.clear_offer_message(1, 100)
    assert (await db.get_user(1)).offer_message_id is None


async def test_scheduler_queries(db):
    await db.register_start(olya(user_id=1, chat_id=1), now=T0, duration=DAY)
    await db.register_start(olya(user_id=2, chat_id=2), now=T0 + 3600, duration=DAY)
    await db.register_start(olya(user_id=3, chat_id=3), now=T0 + 7200, duration=DAY)
    await db.mark_blocked(3, now=T0 + 7300)
    for user_id in (1, 2, 3):
        await db.set_offer_message(user_id, 500 + user_id)

    now = T0 + DAY + 1  # у першого пропозиція завершилась
    assert [u.user_id for u in await db.active_users(now)] == [2]
    assert [u.user_id for u in await db.live_offers(now)] == [2]
    assert [u.user_id for u in await db.pending_expiry(now)] == [1]
    await db.mark_expiry_done(1)
    assert await db.pending_expiry(now) == []
    assert (await db.get_user(1)).expiry_done


async def test_reminder_log_is_written_once(db):
    await db.register_start(olya(), now=T0, duration=DAY)
    await db.log_reminder(1, "r1", REMINDER_SKIPPED, now=T0 + 10)
    await db.log_reminder(1, "r2", REMINDER_SENT, now=T0 + 20)
    await db.log_reminder(1, "r2", REMINDER_SENT, now=T0 + 30)  # дубль ігнорується
    assert await db.reminder_log(T0 + 40) == {1: {"r1", "r2"}}
    assert await db.reminder_log(T0 + DAY) == {}  # після дедлайну журнал планувальнику не потрібен
    assert (await db.stats(T0 + 40)).reminders_sent == 1


async def test_stats(db):
    await db.register_start(olya(user_id=1, chat_id=1), now=T0, duration=DAY, source="instagram")
    await db.register_start(olya(user_id=2, chat_id=2), now=T0 + DAY, duration=DAY, source="instagram")
    await db.register_start(olya(user_id=3, chat_id=3), now=T0 + DAY, duration=DAY)
    await db.mark_blocked(3, now=T0 + DAY)
    await db.add_event(1, EVENT_PACKAGE, now=T0, package_id="vip")
    await db.add_event(2, EVENT_PACKAGE, now=T0 + DAY, package_id="vip")
    await db.add_event(1, EVENT_PAYMENT_LINK, now=T0, package_id="vip")
    await db.add_event(1, EVENT_PAYMENT_LINK, now=T0 + 5, package_id="vip")
    await db.add_event(2, EVENT_PAYMENT_LINK, now=T0 + DAY, package_id="start")

    stats = await db.stats(T0 + DAY + 10)
    assert (stats.users_total, stats.users_last_24h, stats.active, stats.expired, stats.blocked) == (3, 2, 2, 1, 1)
    assert stats.package_views == {"vip": 2}
    assert stats.payment_links == {"vip": (2, 1), "start": (1, 1)}
    assert stats.sources == [("instagram", 2)]


async def test_data_survives_reopen(tmp_path):
    path = tmp_path / "bot.db"
    first = await Storage.open(path)
    await first.register_start(olya(), now=T0, duration=DAY)
    await first.set_offer_message(1, 777)
    await first.log_reminder(1, "r1", REMINDER_SENT, now=T0 + 7200)
    await first.close()

    second = await Storage.open(path)  # «перезапуск сервера»
    try:
        user = await second.get_user(1)
        assert (user.started_at, user.deadline_at, user.offer_message_id) == (T0, T0 + DAY, 777)
        assert await second.reminder_log(T0 + 8000) == {1: {"r1"}}
        _, created = await second.register_start(olya(), now=T0 + 9000, duration=DAY)
        assert not created
    finally:
        await second.close()


async def test_refuses_database_from_newer_version(tmp_path):
    path = tmp_path / "bot.db"
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="новішою версією"):
        await Storage.open(path)
