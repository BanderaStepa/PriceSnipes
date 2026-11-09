"""Безпечні виклики Telegram API: надсилання, редагування, видалення.

Помилки Telegram не «валять» бота, а перетворюються на зрозумілий результат:
  OK       — виконано (або повідомлення вже має такий самий вигляд);
  BLOCKED  — користувач заблокував бота / чат недоступний (позначаємо в БД, більше не пишемо);
  GONE     — повідомлення видалене або його вже не можна редагувати;
  FAILED   — Telegram відхилив запит, повторювати немає сенсу;
  RETRY    — тимчасова проблема (мережа, перевантаження) — варто спробувати пізніше.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.types import InlineKeyboardMarkup, InputFile, LinkPreviewOptions, Message

from .htmltext import strip_tags

log = logging.getLogger(__name__)

# Пауза винесена окремо, щоб тести могли її підмінити, не чіпаючи asyncio.sleep для всього процесу.
_sleep = asyncio.sleep


class Outcome(enum.Enum):
    OK = "ok"
    BLOCKED = "blocked"
    GONE = "gone"
    FAILED = "failed"
    RETRY = "retry"


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    message_id: int | None = None
    file_id: str | None = None  # id надісланого фото в Telegram (щоб наступного разу не завантажувати файл знову)

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK


_NOT_MODIFIED = "message is not modified"
_PARSE_ERROR = "can't parse entities"
_GONE = (
    "message to edit not found",
    "message can't be edited",
    "message to delete not found",
    "message can't be deleted",
    "message_id_invalid",
)
_BLOCKED = (
    "chat not found",
    "user not found",
    "bot was blocked",
    "user is deactivated",
    "bot can't initiate conversation",
    "peer_id_invalid",
    "chat_write_forbidden",
    "have no rights to send",
)

OnBlocked = Callable[[int], Awaitable[None]]


class RateLimiter:
    """Не більше N запитів на секунду — для фонових розсилок (ліміт Telegram ~30/с на бота)."""

    def __init__(self, per_second: float) -> None:
        self._interval = 1.0 / per_second if per_second > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        if not self._interval:
            return
        async with self._lock:
            now = time.monotonic()
            if self._next > now:
                await _sleep(self._next - now)
                now = time.monotonic()
            self._next = max(now, self._next) + self._interval


class Sender:
    MAX_ATTEMPTS = 3
    MAX_RETRY_AFTER = 60  # секунд: довше всередині одного виклику не чекаємо

    def __init__(
        self,
        bot: Bot,
        *,
        link_previews: bool = False,
        on_blocked: OnBlocked | None = None,
        background_rate: float = 20.0,
    ) -> None:
        self.bot = bot
        self._preview = LinkPreviewOptions(is_disabled=not link_previews)
        self._on_blocked = on_blocked
        self._limiter = RateLimiter(background_rate)

    async def send(
        self,
        chat_id: int,
        text: str,
        keyboard: InlineKeyboardMarkup | None = None,
        *,
        silent: bool = False,
        background: bool = False,
        user_chat: bool = True,
    ) -> Result:
        """Надсилає HTML-повідомлення. background=True — для розсилок (з обмеженням швидкості).

        user_chat=False — службовий чат (менеджер): його недоступність не позначає користувача як заблокованого.
        """

        async def call(parse_mode: str | None, body: str) -> Any:
            return await self.bot.send_message(
                chat_id=chat_id,
                text=body,
                parse_mode=parse_mode,
                reply_markup=keyboard,
                link_preview_options=self._preview,
                disable_notification=silent,
            )

        return await self._run("надсилання", chat_id, text, call, background=background, user_chat=user_chat)

    async def send_photo(
        self,
        chat_id: int,
        photo: str | InputFile,
        caption: str = "",
        keyboard: InlineKeyboardMarkup | None = None,
        *,
        background: bool = False,
    ) -> Result:
        """Надсилає фото (файл, посилання або file_id) з необов'язковим HTML-підписом до 1024 символів."""

        async def call(parse_mode: str | None, body: str) -> Any:
            return await self.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=body or None,
                parse_mode=parse_mode if body else None,
                reply_markup=keyboard,
            )

        return await self._run("надсилання фото", chat_id, caption, call, background=background, user_chat=True)

    async def edit(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        keyboard: InlineKeyboardMarkup | None = None,
        *,
        background: bool = True,
    ) -> Result:
        """Редагує повідомлення (і його кнопки: keyboard=None прибирає кнопки)."""

        async def call(parse_mode: str | None, body: str) -> Any:
            return await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=body,
                parse_mode=parse_mode,
                reply_markup=keyboard,
                link_preview_options=self._preview,
            )

        result = await self._run("редагування", chat_id, text, call, background=background, user_chat=True)
        return Result(result.outcome, message_id) if result.ok else result

    async def delete(self, chat_id: int, message_id: int) -> Result:
        try:
            await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramAPIError as e:
            log.debug("Не вдалося видалити повідомлення %s у чаті %s: %s", message_id, chat_id, e)
            return Result(Outcome.GONE)
        return Result(Outcome.OK)

    async def _run(
        self,
        action: str,
        chat_id: int,
        text: str,
        call: Callable[[str | None, str], Awaitable[Any]],
        *,
        background: bool,
        user_chat: bool,
    ) -> Result:
        parse_mode: str | None = "HTML"
        body = text
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            if background:
                await self._limiter.wait()
            try:
                response = await call(parse_mode, body)
            except TelegramRetryAfter as e:
                if e.retry_after > self.MAX_RETRY_AFTER or attempt == self.MAX_ATTEMPTS:
                    log.warning("Telegram просить зачекати %s с (%s, чат %s) — спробую пізніше", e.retry_after, action, chat_id)
                    return Result(Outcome.RETRY)
                await _sleep(e.retry_after)
                continue
            except TelegramForbiddenError as e:
                return await self._blocked(chat_id, user_chat, e.message)
            except TelegramMigrateToChat as e:
                log.error("Чат %s став супергрупою з id %s — оновіть id у налаштуваннях", chat_id, e.migrate_to_chat_id)
                return Result(Outcome.FAILED)
            except TelegramBadRequest as e:
                problem = e.message.lower()
                if _NOT_MODIFIED in problem:
                    return Result(Outcome.OK)
                if _PARSE_ERROR in problem and parse_mode is not None:
                    log.warning("Помилка HTML-розмітки (%s) — надсилаю без форматування. Текст: %.80r", e.message, text)
                    parse_mode, body = None, strip_tags(text)
                    continue
                if any(marker in problem for marker in _GONE):
                    return Result(Outcome.GONE)
                if any(marker in problem for marker in _BLOCKED):
                    return await self._blocked(chat_id, user_chat, e.message)
                log.error("Telegram відхилив запит (%s, чат %s): %s", action, chat_id, e.message)
                return Result(Outcome.FAILED)
            except TelegramUnauthorizedError:
                log.error("Telegram не приймає токен бота — перевірте BOT_TOKEN")
                return Result(Outcome.FAILED)
            except (TelegramNetworkError, TelegramServerError) as e:
                if attempt == self.MAX_ATTEMPTS:
                    log.warning("Telegram тимчасово недоступний (%s, чат %s): %s — спробую пізніше", action, chat_id, e)
                    return Result(Outcome.RETRY)
                await _sleep(attempt)
                continue
            except TelegramAPIError as e:
                log.error("Помилка Telegram (%s, чат %s): %s", action, chat_id, e)
                return Result(Outcome.FAILED)
            message_id = response.message_id if isinstance(response, Message) else None
            file_id = response.photo[-1].file_id if isinstance(response, Message) and response.photo else None
            return Result(Outcome.OK, message_id, file_id)
        return Result(Outcome.RETRY)

    async def _blocked(self, chat_id: int, user_chat: bool, reason: str) -> Result:
        if user_chat and self._on_blocked is not None:
            log.info("Користувач %s недоступний (%s) — більше йому не пишемо", chat_id, reason)
            await self._on_blocked(chat_id)
        else:
            log.warning("Не вдалося надіслати в чат %s: %s", chat_id, reason)
        return Result(Outcome.BLOCKED)
