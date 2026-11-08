"""Збирання бота і запуск: БД → Telegram → воронка → планувальник → polling."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.base import BaseSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError
from aiogram.types import BotCommand, User

from .admin import create_admin_router
from .config import Settings
from .content import Content
from .funnel import Funnel
from .handlers import create_router
from .scheduler import Scheduler
from .sender import Sender
from .storage import Storage
from .timeutils import Clock

log = logging.getLogger(__name__)
_sleep = asyncio.sleep  # окремо, щоб тести могли підмінити паузу


@dataclass
class App:
    settings: Settings
    content: Content
    bot: Bot
    dp: Dispatcher
    storage: Storage
    funnel: Funnel
    scheduler: Scheduler

    async def close(self) -> None:
        await self.scheduler.stop()
        await self.storage.close()
        await self.bot.session.close()


async def build_app(
    settings: Settings,
    content: Content,
    *,
    session: BaseSession | None = None,
    clock: Clock | None = None,
    background_rate: float = 20.0,
) -> App:
    clock = clock or Clock()
    storage = await Storage.open(settings.db_path)
    if session is None:
        api = TelegramAPIServer.from_base(settings.telegram_api_url) if settings.telegram_api_url else None
        session = AiohttpSession(api=api) if api else AiohttpSession()
    bot = Bot(settings.bot_token, session=session)

    async def on_blocked(chat_id: int) -> None:
        await storage.mark_blocked(chat_id, now=clock.now())

    sender = Sender(bot, link_previews=content.settings.link_previews, on_blocked=on_blocked,
                    background_rate=background_rate)
    funnel = Funnel(storage=storage, content=content, sender=sender, clock=clock,
                    manager_chat_id=settings.manager_chat_id)
    scheduler = Scheduler(funnel, tick_seconds=settings.scheduler_tick)
    dp = Dispatcher(funnel=funnel, settings=settings)
    dp.include_router(create_admin_router())  # службові команди — раніше за «будь-яке повідомлення»
    dp.include_router(create_router())
    return App(settings=settings, content=content, bot=bot, dp=dp, storage=storage, funnel=funnel, scheduler=scheduler)


async def wait_for_telegram(bot: Bot, *, first_delay: float = 5.0, max_delay: float = 60.0) -> User:
    """Перевіряє токен (getMe). Якщо Telegram недоступний — чекає і пробує знову, а не падає.

    Невірний токен (TelegramUnauthorizedError) — одразу помилка: чекати немає сенсу.
    """
    delay = first_delay
    while True:
        try:
            return await bot.get_me()
        except TelegramUnauthorizedError:
            raise
        except TelegramAPIError as e:
            log.warning("Telegram поки недоступний (%s) — повтор через %.0f с", e, delay)
        await _sleep(delay)
        delay = min(delay * 2, max_delay)


async def run(app: App, *, handle_signals: bool = True) -> None:
    """Запускає бота і працює до зупинки (Ctrl+C або SIGTERM від хостингу)."""
    try:
        me = await wait_for_telegram(app.bot)
        log.info("Бот @%s запущено. База даних: %s", me.username, app.settings.db_path)
        try:
            await app.bot.delete_webhook(drop_pending_updates=False)  # для polling вебхук має бути вимкнено
            await app.bot.set_my_commands(
                [BotCommand(command="start", description=app.content.settings.start_command_description)]
            )
        except TelegramAPIError as e:
            log.warning("Не вдалося оновити меню команд: %s", e)
        app.scheduler.start()
        await app.dp.start_polling(
            app.bot,
            allowed_updates=app.dp.resolve_used_update_types(),
            close_bot_session=False,
            handle_signals=handle_signals,
        )
    finally:
        await app.close()
