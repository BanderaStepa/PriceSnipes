"""Збирає бота для тестів: справжні Funnel, Storage, Sender і диспетчер aiogram, фейковий Telegram і годинник."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.types import User

from bot.content import Content
from bot.funnel import Funnel
from bot.handlers import create_router
from bot.sender import Sender
from bot.storage import Storage
from tests.fake_telegram import FakeClock, FakeTelegram, callback_update, message_update

DAY = 86400
# 2026-11-08 12:00:00 за Києвом (UTC+2)
T0 = 1_794_132_000


@dataclass
class Harness:
    telegram: FakeTelegram
    bot: Bot
    storage: Storage
    funnel: Funnel
    dp: Dispatcher
    clock: FakeClock
    content: Content

    async def send(self, text: str, user: User | None = None, **kwargs) -> None:
        await self.dp.feed_update(self.bot, message_update(text, user, **kwargs))

    async def press(self, data: str, user: User | None = None, **kwargs) -> None:
        await self.dp.feed_update(self.bot, callback_update(data, user, **kwargs))

    async def close(self) -> None:
        await self.storage.close()
        await self.bot.session.close()


async def make_harness(content: Content, db_path: Path, *, start: int = T0, manager_chat_id: int | None = None,
                       telegram: FakeTelegram | None = None, clock: FakeClock | None = None) -> Harness:
    telegram = telegram or FakeTelegram()
    clock = clock or FakeClock(start)
    bot = Bot("123456:TEST-TOKEN", session=telegram)
    storage = await Storage.open(db_path)

    async def on_blocked(chat_id: int) -> None:
        await storage.mark_blocked(chat_id, now=clock.now())

    sender = Sender(bot, link_previews=content.settings.link_previews, on_blocked=on_blocked, background_rate=0)
    funnel = Funnel(storage=storage, content=content, sender=sender, clock=clock, manager_chat_id=manager_chat_id)
    dp = Dispatcher(funnel=funnel)
    dp.include_router(create_router())
    return Harness(telegram=telegram, bot=bot, storage=storage, funnel=funnel, dp=dp, clock=clock, content=content)
