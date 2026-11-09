"""Фейковий Telegram Bot API для тестів: без мережі, записує всі запити і повертає правдоподібні відповіді."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import DeleteMessage, EditMessageText, GetMe, GetUpdates, SendMessage, SendPhoto, TelegramMethod
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, MessageEntity, PhotoSize, Update, User

from bot.timeutils import Clock

BOT_USER = User(id=777000, is_bot=True, first_name="Для рішучих", username="dlya_rishuchyh_bot")
_update_ids = itertools.count(1)
_incoming_ids = itertools.count(1)


class FakeClock(Clock):
    def __init__(self, start: int) -> None:
        self.t = start

    def now(self) -> int:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += int(seconds)


class FakeTelegram(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        self.blocked: set[int] = set()  # чати, які «заблокували бота»
        self._failures: list[tuple[type, Callable[[TelegramMethod[Any]], Exception]]] = []
        self._message_ids = itertools.count(1001)

    def fail_next(self, method_type: type, error: Callable[[TelegramMethod[Any]], Exception]) -> None:
        """Наступний запит цього типу завершиться помилкою error(method)."""
        self._failures.append((method_type, error))

    async def close(self) -> None:
        pass

    async def stream_content(self, url: str, headers: dict[str, Any] | None = None, timeout: int = 30,
                             chunk_size: int = 65536, raise_for_status: bool = True) -> AsyncGenerator[bytes, None]:
        yield b""

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:
        self.requests.append(method)
        for index, (method_type, error) in enumerate(self._failures):
            if isinstance(method, method_type):
                del self._failures[index]
                raise error(method)
        chat_id = getattr(method, "chat_id", None)
        if chat_id in self.blocked and isinstance(method, (SendMessage, SendPhoto, EditMessageText)):
            raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
        now = datetime.now(UTC)
        if isinstance(method, SendMessage):
            return Message(message_id=next(self._message_ids), date=now, chat=Chat(id=method.chat_id, type="private"),
                           from_user=BOT_USER, text=method.text)
        if isinstance(method, SendPhoto):
            number = next(self._message_ids)
            # як у Telegram: фото, надіслане за file_id, має той самий file_id; новий файл — новий id
            file_id = method.photo if isinstance(method.photo, str) else f"photo-{number}"
            size = PhotoSize(file_id=file_id, file_unique_id=f"unique-{file_id}", width=1280, height=955)
            return Message(message_id=number, date=now, chat=Chat(id=method.chat_id, type="private"),
                           from_user=BOT_USER, caption=method.caption, photo=[size])
        if isinstance(method, EditMessageText):
            return Message(message_id=method.message_id, date=now, chat=Chat(id=method.chat_id, type="private"),
                           from_user=BOT_USER, text=method.text)
        if isinstance(method, GetMe):
            return BOT_USER
        if isinstance(method, GetUpdates):
            await asyncio.sleep(0.01)  # «довге опитування» без нових оновлень
            return []
        return True  # answerCallbackQuery, deleteMessage, setMyCommands, deleteWebhook…

    # ------------------------------------------------------------------ вибірки для перевірок

    def calls(self, method_type: type) -> list[Any]:
        return [r for r in self.requests if isinstance(r, method_type)]

    def sent(self, chat_id: int | None = None) -> list[SendMessage]:
        return [r for r in self.calls(SendMessage) if chat_id is None or r.chat_id == chat_id]

    def photos(self, chat_id: int | None = None) -> list[SendPhoto]:
        return [r for r in self.calls(SendPhoto) if chat_id is None or r.chat_id == chat_id]

    def edits(self, chat_id: int | None = None) -> list[EditMessageText]:
        return [r for r in self.calls(EditMessageText) if chat_id is None or r.chat_id == chat_id]

    def deleted(self) -> list[DeleteMessage]:
        return self.calls(DeleteMessage)

    def clear(self) -> None:
        self.requests.clear()


def keyboard_of(markup: InlineKeyboardMarkup | None) -> list[list[tuple[str, str | None]]] | None:
    """Клавіатура → [[(текст, callback_data або url), …], …]."""
    if markup is None:
        return None
    return [[(b.text, b.callback_data or b.url) for b in row] for row in markup.inline_keyboard]


def tg_user(user_id: int = 1, first_name: str = "Оля", username: str | None = "olya") -> User:
    return User(id=user_id, is_bot=False, first_name=first_name, username=username, language_code="uk")


def message_update(text: str, user: User | None = None, *, chat_type: str = "private",
                   chat_id: int | None = None) -> Update:
    user = user or tg_user()
    entities = None
    if text.startswith("/"):
        entities = [MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))]
    message = Message(
        message_id=next(_incoming_ids),
        date=datetime.now(UTC),
        chat=Chat(id=user.id if chat_id is None else chat_id, type=chat_type),
        from_user=user,
        text=text,
        entities=entities,
    )
    return Update(update_id=next(_update_ids), message=message)


def callback_update(data: str, user: User | None = None, *, message_id: int = 1) -> Update:
    user = user or tg_user()
    message = Message(message_id=message_id, date=datetime.now(UTC), chat=Chat(id=user.id, type="private"),
                      from_user=BOT_USER, text="…")
    query = CallbackQuery(id=f"cb{next(_update_ids)}", from_user=user, chat_instance="test", data=data,
                          message=message)
    return Update(update_id=next(_update_ids), callback_query=query)
