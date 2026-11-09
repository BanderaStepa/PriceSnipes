"""Збирає бота для тестів тим самим build_app, що й справжній запуск, але з фейковим Telegram і годинником."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.types import User

from bot.app import App, build_app
from bot.config import Settings
from bot.content import Content
from bot.funnel import Funnel
from bot.scheduler import Scheduler
from bot.storage import Storage
from tests.fake_telegram import FakeClock, FakeTelegram, callback_update, message_update

# Еталонний контент для тестів (копія content.yaml на момент написання тестів).
# Справжній content.yaml належить замовнику і може змінюватися як завгодно — тести від нього не залежать.
REFERENCE_CONTENT_PATH = Path(__file__).resolve().parent / "fixtures" / "content.yaml"
DAY = 86400
# 2026-11-08 12:00:00 за Києвом (UTC+2)
T0 = 1_794_132_000
TOKEN = "123456:TEST-TOKEN"


@dataclass
class Harness:
    app: App
    telegram: FakeTelegram
    clock: FakeClock

    @property
    def bot(self) -> Bot:
        return self.app.bot

    @property
    def dp(self) -> Dispatcher:
        return self.app.dp

    @property
    def storage(self) -> Storage:
        return self.app.storage

    @property
    def funnel(self) -> Funnel:
        return self.app.funnel

    @property
    def scheduler(self) -> Scheduler:
        return self.app.scheduler

    @property
    def content(self) -> Content:
        return self.app.content

    async def send(self, text: str, user: User | None = None, **kwargs) -> None:
        await self.dp.feed_update(self.bot, message_update(text, user, **kwargs))

    async def press(self, data: str, user: User | None = None, **kwargs) -> None:
        await self.dp.feed_update(self.bot, callback_update(data, user, **kwargs))

    async def close(self) -> None:
        await self.app.close()


async def make_harness(content: Content, db_path: Path, *, start: int = T0, manager_chat_id: int | None = None,
                       admin_ids: frozenset[int] = frozenset(), telegram: FakeTelegram | None = None,
                       clock: FakeClock | None = None) -> Harness:
    telegram = telegram or FakeTelegram()
    clock = clock or FakeClock(start)
    settings = Settings(bot_token=TOKEN, db_path=db_path, manager_chat_id=manager_chat_id,
                        admin_ids=frozenset(admin_ids), scheduler_tick=0.01)
    app = await build_app(settings, content, session=telegram, clock=clock, background_rate=0)
    return Harness(app=app, telegram=telegram, clock=clock)
