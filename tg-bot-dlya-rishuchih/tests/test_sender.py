"""Обробка помилок Telegram у Sender."""

import pytest
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import DeleteMessage, EditMessageText, SendMessage

from bot import sender as sender_module
from bot.sender import Outcome, Sender
from tests.fake_telegram import FakeTelegram


@pytest.fixture
def sleeps(monkeypatch):
    """Підміняє asyncio.sleep у sender.py: тести не чекають по-справжньому."""
    calls: list[float] = []

    async def fake_sleep(seconds):
        calls.append(seconds)

    monkeypatch.setattr(sender_module.asyncio, "sleep", fake_sleep)
    return calls


@pytest.fixture
def telegram():
    return FakeTelegram()


@pytest.fixture
def blocked_calls():
    return []


@pytest.fixture
def sender(telegram, blocked_calls):
    async def on_blocked(chat_id):
        blocked_calls.append(chat_id)

    return Sender(Bot("123456:TEST-TOKEN", session=telegram), on_blocked=on_blocked, background_rate=0)


def bad_request(text):
    return lambda m: TelegramBadRequest(method=m, message=f"Bad Request: {text}")


async def test_send_ok_returns_message_id(sender, telegram):
    result = await sender.send(1, "<b>Привіт</b>", silent=True)
    assert result.ok and result.message_id
    [call] = telegram.sent()
    assert call.parse_mode == "HTML" and call.disable_notification is True
    assert call.link_preview_options.is_disabled is True


async def test_not_modified_counts_as_success(sender, telegram):
    telegram.fail_next(EditMessageText, bad_request("message is not modified: specified new message content..."))
    assert (await sender.edit(1, 10, "текст")).outcome is Outcome.OK


async def test_missing_message_is_gone(sender, telegram):
    telegram.fail_next(EditMessageText, bad_request("message to edit not found"))
    assert (await sender.edit(1, 10, "текст")).outcome is Outcome.GONE


async def test_blocked_user_is_reported(sender, telegram, blocked_calls):
    telegram.blocked.add(7)
    assert (await sender.send(7, "текст")).outcome is Outcome.BLOCKED
    assert blocked_calls == [7]


async def test_chat_not_found_is_treated_as_blocked(sender, telegram, blocked_calls):
    telegram.fail_next(SendMessage, bad_request("chat not found"))
    assert (await sender.send(8, "текст")).outcome is Outcome.BLOCKED
    assert blocked_calls == [8]


async def test_service_chat_problem_does_not_block_anyone(sender, telegram, blocked_calls):
    telegram.fail_next(SendMessage, lambda m: TelegramForbiddenError(method=m, message="Forbidden: bot can't initiate conversation with a user"))
    assert (await sender.send(-100, "текст", user_chat=False)).outcome is Outcome.BLOCKED
    assert blocked_calls == []


async def test_flood_control_waits_and_retries(sender, telegram, sleeps):
    telegram.fail_next(SendMessage, lambda m: TelegramRetryAfter(method=m, message="Too Many Requests", retry_after=3))
    assert (await sender.send(1, "текст")).ok
    assert sleeps == [3]
    assert len(telegram.sent()) == 2


async def test_long_flood_wait_is_postponed(sender, telegram, sleeps):
    telegram.fail_next(SendMessage, lambda m: TelegramRetryAfter(method=m, message="Too Many Requests", retry_after=600))
    assert (await sender.send(1, "текст")).outcome is Outcome.RETRY
    assert sleeps == []


async def test_network_problems_are_retried_then_postponed(sender, telegram, sleeps):
    for _ in range(3):
        telegram.fail_next(SendMessage, lambda m: TelegramNetworkError(method=m, message="timeout"))
    assert (await sender.send(1, "текст")).outcome is Outcome.RETRY
    assert sleeps == [1, 2]


async def test_server_error_once_then_success(sender, telegram, sleeps):
    telegram.fail_next(SendMessage, lambda m: TelegramServerError(method=m, message="Internal Server Error"))
    assert (await sender.send(1, "текст")).ok
    assert sleeps == [1]


async def test_other_bad_request_fails_without_retry(sender, telegram):
    telegram.fail_next(SendMessage, bad_request("BUTTON_URL_INVALID"))
    assert (await sender.send(1, "текст")).outcome is Outcome.FAILED
    assert len(telegram.sent()) == 1


async def test_parse_error_falls_back_to_plain_text(sender, telegram):
    telegram.fail_next(SendMessage, bad_request("can't parse entities: Unsupported start tag"))
    assert (await sender.send(1, "<b>Ціна</b> &lt;5000&gt;")).ok
    first, second = telegram.sent()
    assert (first.parse_mode, second.parse_mode) == ("HTML", None)
    assert second.text == "Ціна <5000>"


async def test_delete_never_raises(sender, telegram):
    telegram.fail_next(DeleteMessage, bad_request("message can't be deleted for everyone"))
    assert (await sender.delete(1, 5)).outcome is Outcome.GONE
    assert (await sender.delete(1, 6)).ok
