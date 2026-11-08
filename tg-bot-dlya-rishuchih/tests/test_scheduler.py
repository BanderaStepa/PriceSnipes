"""Планувальник: нагадування, живий таймер, завершення пропозиції, робота після перезапуску."""

import asyncio

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import EditMessageText, SendMessage

from bot import sender as sender_module
from bot.content import load_content
from bot.paths import DEFAULT_CONTENT_PATH
from tests.fake_telegram import keyboard_of, tg_user
from tests.harness import DAY, T0, make_harness

HOUR = 3600


async def started(harness, user=None):
    await harness.send("/start", user)
    harness.telegram.clear()


async def at(harness, offset):
    """Перевести годинник на T0 + offset і зробити прохід планувальника."""
    harness.clock.t = T0 + offset
    await harness.scheduler.tick()


@pytest.fixture
def no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(sender_module, "_sleep", instant)


# ----------------------------------------------------------------------------- нагадування


async def test_reminders_follow_schedule_with_time_left(harness):
    # T0 = 12:00 за Києвом: r1 о 14:00, r2 о 00:00 (тихі години), r3 о 09:00, r4 об 11:00
    await started(harness)
    await at(harness, 2 * HOUR - 60)
    assert harness.telegram.sent() == []

    await at(harness, 2 * HOUR)
    [r1] = harness.telegram.sent(1)
    assert "пропозиція для рішучих діє ще <b>22 год</b>" in r1.text
    assert keyboard_of(r1.reply_markup) == [[("Обрати пакет", "packages")]]
    assert r1.disable_notification is False
    harness.telegram.clear()
    await at(harness, 2 * HOUR + 30)
    assert harness.telegram.sent() == []  # без дублів

    await at(harness, 12 * HOUR)
    [r2] = harness.telegram.sent(1)
    assert "До кінця пропозиції для рішучих — <b>12 год</b>" in r2.text
    assert r2.disable_notification is True  # північ — тихі години, без звуку
    harness.telegram.clear()

    await at(harness, 21 * HOUR)
    [r3] = harness.telegram.sent(1)
    assert "Залишилось лише <b>3 год</b>" in r3.text
    harness.telegram.clear()

    await at(harness, 23 * HOUR)
    [r4] = harness.telegram.sent(1)
    assert "Остання година! Пропозиція для рішучих діє до 9 листопада о 12:00." in r4.text
    assert "Залишилось <b>1 год</b>" in r4.text
    harness.telegram.clear()

    for offset in (DAY, DAY + HOUR, 3 * DAY):  # після 24 год нагадування припиняються
        await at(harness, offset)
    assert harness.telegram.sent() == []
    assert (await harness.storage.stats(harness.clock.now())).reminders_sent == 4


async def test_after_downtime_only_latest_due_reminder_is_sent(harness):
    await started(harness)
    await at(harness, 13 * HOUR)  # сервер «лежав»: настав час і r1 (+2 год), і r2 (+12 год)
    [message] = harness.telegram.sent(1)
    assert "Минула половина часу" in message.text
    assert await harness.storage.reminder_log(harness.clock.now()) == {1: {"r1", "r2"}}
    assert (await harness.storage.stats(harness.clock.now())).reminders_sent == 1


async def test_reminders_are_silent_at_night(harness):
    harness.clock.t = T0 + 9 * HOUR  # /start о 21:00
    await started(harness)
    await at(harness, 11 * HOUR)  # r1 о 23:00
    [r1] = harness.telegram.sent(1)
    assert r1.disable_notification is True


async def test_silent_hours_can_be_disabled(make):
    harness = await make(lambda raw: raw["settings"].update(silent_hours=""))
    await started(harness)
    await at(harness, 12 * HOUR)  # r2 опівночі
    [r2] = harness.telegram.sent(1)
    assert r2.disable_notification is False


async def test_each_user_gets_reminders_by_own_timer(harness):
    for index in range(40):
        harness.clock.t = T0 + index * 600  # нові користувачі кожні 10 хв
        await harness.send("/start", tg_user(100 + index, f"Користувач {index}"))
    harness.telegram.clear()
    await at(harness, 2 * HOUR + 39 * 600)  # для всіх 40 настав час r1
    assert sorted(m.chat_id for m in harness.telegram.sent()) == list(range(100, 140))
    harness.telegram.clear()
    await at(harness, 2 * HOUR + 39 * 600 + 15)
    assert harness.telegram.sent() == []


async def test_blocked_user_gets_nothing_until_returns(harness):
    await started(harness)
    harness.telegram.blocked.add(1)
    await at(harness, 2 * HOUR)
    assert (await harness.storage.get_user(1)).is_blocked
    harness.telegram.clear()
    await at(harness, 2 * HOUR + 60)
    assert harness.telegram.requests == []  # ні нагадувань, ні оновлень таймера

    harness.telegram.blocked.discard(1)
    await harness.send("/start")  # розблокував і повернувся
    assert not (await harness.storage.get_user(1)).is_blocked
    harness.telegram.clear()
    await at(harness, 12 * HOUR)
    [r2] = harness.telegram.sent(1)
    assert "Минула половина часу" in r2.text


async def test_temporary_failure_is_retried_on_next_pass(harness, no_sleep):
    await started(harness)
    for _ in range(3):
        harness.telegram.fail_next(SendMessage, lambda m: TelegramNetworkError(method=m, message="timeout"))
    await at(harness, 2 * HOUR)
    assert await harness.storage.reminder_log(harness.clock.now()) == {}  # не позначено — спробуємо ще
    harness.telegram.clear()
    await at(harness, 2 * HOUR + 15)
    [r1] = harness.telegram.sent(1)
    assert "діє ще" in r1.text


# ----------------------------------------------------------------------------- живий таймер


async def test_live_timer_updates_once_a_minute(harness):
    await started(harness)
    offer_id = (await harness.storage.get_user(1)).offer_message_id
    await at(harness, 30)
    assert harness.telegram.edits() == []  # усе ще «24 год»

    await at(harness, 61)
    [edit] = harness.telegram.edits(1)
    assert edit.message_id == offer_id
    assert "⏳ Залишилось: <b>23 год 59 хв</b> (до 9 листопада о 12:00)" in edit.text
    assert keyboard_of(edit.reply_markup) == [[("Обрати пакет", "packages")]]  # кнопка лишається
    harness.telegram.clear()

    await at(harness, 90)
    assert harness.telegram.edits() == []
    await at(harness, 125)
    [edit] = harness.telegram.edits(1)
    assert "<b>23 год 58 хв</b>" in edit.text


async def test_timer_update_interval_from_config(make):
    harness = await make(lambda raw: raw["settings"].update(timer_update_every="5 хв"))
    await started(harness)
    for minute in range(1, 11):
        await at(harness, minute * 60 + 1)
    assert len(harness.telegram.edits()) == 2  # на 5-й і 10-й хвилині


async def test_live_timer_can_be_disabled(make):
    harness = await make(lambda raw: raw["settings"].update(timer_update_every=0))
    await started(harness)
    await at(harness, HOUR)
    assert harness.telegram.edits() == []


async def test_deleted_offer_is_left_alone(harness):
    await started(harness)
    harness.telegram.fail_next(
        EditMessageText, lambda m: TelegramBadRequest(method=m, message="Bad Request: message to edit not found")
    )
    await at(harness, 61)
    assert (await harness.storage.get_user(1)).offer_message_id is None
    harness.telegram.clear()
    await at(harness, 125)
    assert harness.telegram.edits() == []


# ----------------------------------------------------------------------------- завершення пропозиції


async def test_offer_expires_after_24_hours(harness):
    await started(harness)
    offer_id = (await harness.storage.get_user(1)).offer_message_id
    await at(harness, DAY - 1)
    harness.telegram.clear()

    await at(harness, DAY)
    [edit] = harness.telegram.edits(1)
    assert edit.message_id == offer_id
    assert edit.text.endswith("⌛️ Час вийшов — пропозиція завершилась.")
    assert edit.reply_markup is None  # кнопку «Обрати пакет» прибрано
    assert harness.telegram.sent() == []  # send_expired_notice: false — без окремого повідомлення
    assert (await harness.storage.get_user(1)).expiry_done

    harness.telegram.clear()
    await at(harness, DAY + HOUR)
    assert harness.telegram.requests == []  # далі бот нічого не надсилає


async def test_expired_notice_is_sent_once_when_enabled(make):
    harness = await make(lambda raw: raw["settings"].update(send_expired_notice=True))
    await started(harness)
    await at(harness, DAY + 20)
    [notice] = harness.telegram.sent(1)
    assert notice.text.startswith("⌛️ Оля, пропозиція для рішучих завершилась.")
    harness.telegram.clear()
    await at(harness, DAY + 60)
    assert harness.telegram.sent() == []


async def test_no_stale_expired_notice_after_long_downtime(make):
    harness = await make(lambda raw: raw["settings"].update(send_expired_notice=True))
    await started(harness)
    await at(harness, DAY + 3 * HOUR)
    assert harness.telegram.sent() == []
    assert len(harness.telegram.edits(1)) == 1  # але таймер в офері оновлено


async def test_offer_keeps_button_when_sales_continue(make):
    def change(raw):
        raw["settings"]["packages_after_expiry"] = True
        raw["keyboards"]["expired"] = [["choose_package"]]

    harness = await make(change)
    await started(harness)
    await at(harness, DAY)
    [edit] = harness.telegram.edits(1)
    assert "Час вийшов" in edit.text
    assert keyboard_of(edit.reply_markup) == [[("Обрати пакет", "packages")]]


# ----------------------------------------------------------------------------- перезапуск сервера


async def test_timer_and_reminders_survive_restart(tmp_path):
    content = load_content(DEFAULT_CONTENT_PATH)
    db_path = tmp_path / "bot.db"

    first = await make_harness(content, db_path)
    await first.send("/start")
    offer_id = (await first.storage.get_user(1)).offer_message_id
    first.clock.t = T0 + 2 * HOUR
    await first.scheduler.tick()
    assert len(first.telegram.sent(1)) == 2  # офер + r1
    await first.close()  # сервер зупинився

    second = await make_harness(content, db_path, start=T0 + 12 * HOUR)  # новий процес, порожня пам'ять
    try:
        await second.scheduler.tick()
        [r2] = second.telegram.sent(1)
        assert "Минула половина часу" in r2.text  # r1 не повторився
        [edit] = second.telegram.edits(1)
        assert edit.message_id == offer_id and "<b>12 год</b>" in edit.text  # той самий живий таймер

        second.telegram.clear()
        await second.send("/start")
        [offer] = second.telegram.sent(1)
        assert "⏳ Залишилось: <b>12 год</b>" in offer.text  # повторний /start не перезапустив таймер

        second.clock.t = T0 + DAY
        await second.scheduler.tick()
        assert (await second.storage.get_user(1)).expiry_done
    finally:
        await second.close()


async def test_background_loops_survive_errors(harness, monkeypatch):
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("тимчасовий збій")

    monkeypatch.setattr(harness.scheduler, "_reminders_and_expiry", flaky)
    harness.scheduler.start()
    await asyncio.sleep(0.1)
    await harness.scheduler.stop()
    assert len(calls) >= 2  # після помилки цикл продовжив роботу
