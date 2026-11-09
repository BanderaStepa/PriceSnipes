"""Обробники Telegram-оновлень: переводять натискання і повідомлення у виклики Funnel."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message, User

from .funnel import Funnel
from .storage import Profile
from .texts import CB_COURSE, CB_GIFT, CB_PACKAGE, CB_PACKAGES, CB_PAY

log = logging.getLogger(__name__)


def profile_of(user: User, chat_id: int) -> Profile:
    return Profile(
        user_id=user.id,
        chat_id=chat_id,
        first_name=user.first_name or "",
        last_name=user.last_name,
        username=user.username,
        language_code=user.language_code,
    )


def _chat_id(callback: CallbackQuery) -> int:
    return callback.message.chat.id if callback.message is not None else callback.from_user.id


async def _answer(callback: CallbackQuery, text: str | None = None) -> None:
    """Прибирає «годинник» на кнопці. Застарілий запит — не помилка."""
    try:
        await callback.answer(text=text)
    except TelegramAPIError as e:
        log.debug("Не вдалося відповісти на натискання кнопки: %s", e)


def create_router() -> Router:
    """Новий роутер для кожного диспетчера (aiogram не дозволяє підключати один роутер двічі)."""
    router = Router(name="funnel")
    private = F.chat.type == ChatType.PRIVATE

    @router.message(CommandStart(), private)
    async def on_start(message: Message, command: CommandObject, funnel: Funnel) -> None:
        if message.from_user is None:
            return
        await funnel.start(profile_of(message.from_user, message.chat.id), source=command.args)

    @router.callback_query(F.data == CB_PACKAGES)
    async def on_packages(callback: CallbackQuery, funnel: Funnel) -> None:
        await _answer(callback)
        await funnel.show_packages(profile_of(callback.from_user, _chat_id(callback)))

    @router.callback_query(F.data.startswith(CB_PACKAGE))
    async def on_package(callback: CallbackQuery, funnel: Funnel) -> None:
        package_id = (callback.data or "").removeprefix(CB_PACKAGE)
        await _answer(callback, None if funnel.is_known_package(package_id) else funnel.texts.package_unavailable())
        await funnel.show_package(profile_of(callback.from_user, _chat_id(callback)), package_id)

    @router.callback_query(F.data.startswith(CB_PAY))
    async def on_pay(callback: CallbackQuery, funnel: Funnel) -> None:
        package_id = (callback.data or "").removeprefix(CB_PAY)
        await _answer(callback, None if funnel.is_known_package(package_id) else funnel.texts.package_unavailable())
        await funnel.send_payment_link(profile_of(callback.from_user, _chat_id(callback)), package_id)

    @router.callback_query(F.data == CB_GIFT)
    async def on_gift(callback: CallbackQuery, funnel: Funnel) -> None:
        await _answer(callback)
        await funnel.show_gift(profile_of(callback.from_user, _chat_id(callback)))

    @router.callback_query(F.data == CB_COURSE)
    async def on_course_info(callback: CallbackQuery, funnel: Funnel) -> None:
        await _answer(callback)
        await funnel.show_course_info(profile_of(callback.from_user, _chat_id(callback)))

    @router.callback_query()
    async def on_unknown_button(callback: CallbackQuery) -> None:
        await _answer(callback)

    @router.message(private)
    async def on_other_message(message: Message, funnel: Funnel) -> None:
        if message.from_user is None or message.from_user.is_bot:
            return
        await funnel.on_text(profile_of(message.from_user, message.chat.id))

    return router
