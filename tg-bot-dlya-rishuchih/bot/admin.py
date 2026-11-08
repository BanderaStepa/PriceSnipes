"""Службові команди: /myid (дізнатися id для налаштувань) і /stats (статистика для адміністраторів)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from .config import Settings
from .content import Content
from .funnel import Funnel
from .handlers import profile_of
from .htmltext import esc
from .storage import Stats


def format_stats(stats: Stats, content: Content, settings: Settings) -> str:
    lines = [
        "📊 <b>Статистика</b>",
        f"Користувачів усього: {stats.users_total}",
        f"Нових за 24 год: {stats.users_last_24h}",
        f"Пропозиція діє: {stats.active} · завершилась: {stats.expired}",
        f"Заблокували бота: {stats.blocked}",
        f"Надіслано нагадувань: {stats.reminders_sent}",
        "",
        "<b>Пакети</b> — переглядів / запитів посилання / людей:",
    ]
    known = [p.id for p in content.packages]
    removed = (set(stats.package_views) | set(stats.payment_links)) - set(known)
    for package_id in known + sorted(removed):
        package = content.package(package_id)
        name = package.name if package else f"{package_id} (видалений)"
        requests, people = stats.payment_links.get(package_id, (0, 0))
        lines.append(f"• {esc(name)}: {stats.package_views.get(package_id, 0)} / {requests} / {people}")
    if stats.sources:
        lines += ["", "<b>Джерела</b> (посилання t.me/бот?start=…):"]
        lines += [f"• {esc(source)}: {count}" for source, count in stats.sources]
    lines += ["", f"База даних: <code>{esc(settings.db_path)}</code>"]
    lines += [f"⚠️ {esc(warning)}" for warning in settings.warnings]
    return "\n".join(lines)


def create_admin_router() -> Router:
    router = Router(name="admin")

    @router.message(Command("myid"))
    async def cmd_myid(message: Message, funnel: Funnel) -> None:
        lines = []
        if message.from_user is not None:
            lines.append(f"Ваш id: <code>{message.from_user.id}</code>")
        if message.chat.type != ChatType.PRIVATE:
            lines.append(f"Id цього чату: <code>{message.chat.id}</code>")
        lines.append("Це число можна вказати в MANAGER_CHAT_ID (куди слати сповіщення) або в ADMIN_IDS (доступ до /stats).")
        await funnel.sender.send(message.chat.id, "\n".join(lines), user_chat=False)

    @router.message(Command("stats"), F.chat.type == ChatType.PRIVATE)
    async def cmd_stats(message: Message, funnel: Funnel, settings: Settings) -> None:
        if message.from_user is None:
            return
        if message.from_user.id not in settings.admin_ids:
            await funnel.on_text(profile_of(message.from_user, message.chat.id))  # для інших — звичайне повідомлення
            return
        stats = await funnel.storage.stats(funnel.clock.now())
        await funnel.sender.send(message.chat.id, format_stats(stats, funnel.content, settings), user_chat=False)

    return router
