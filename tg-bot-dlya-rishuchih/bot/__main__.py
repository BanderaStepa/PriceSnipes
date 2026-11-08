"""Запуск бота:  python -m bot

Налаштування — змінні середовища (або файл .env у папці проєкту), контент — content.yaml.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from aiogram.exceptions import TelegramUnauthorizedError
from dotenv import load_dotenv

from .app import build_app, run
from .config import Settings, SettingsError, load_settings
from .content import Content, ContentError, load_content
from .paths import PROJECT_ROOT

log = logging.getLogger("bot")


def setup_logging(level: str) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    # aiogram пише рядок на кожне оновлення — залишаємо лише попередження (крім режиму DEBUG)
    logging.getLogger("aiogram.event").setLevel(logging.DEBUG if level == "DEBUG" else logging.WARNING)


async def _main(settings: Settings, content: Content) -> None:
    app = await build_app(settings, content)
    await run(app)


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    level = (os.environ.get("LOG_LEVEL") or "INFO").strip().upper()
    setup_logging(level if level in ("DEBUG", "INFO", "WARNING", "ERROR") else "INFO")

    try:
        settings = load_settings()
    except SettingsError as e:
        for error in e.errors:
            log.error(error)
        return 2
    for warning in settings.warnings:
        log.warning(warning)

    try:
        content = load_content(settings.content_path)
    except ContentError as e:
        log.error("%s\nБот не запущено: виправте файл і запустіть знову (перевірка: python -m bot.check_content).",
                  e.report())
        return 2
    for warning in content.warnings:
        log.warning("content.yaml — %s", warning)
    log.info("Контент: %s пакет(и), %s нагадування", len(content.packages), len(content.reminders))

    try:
        asyncio.run(_main(settings, content))
    except TelegramUnauthorizedError:
        log.error("Telegram не приймає BOT_TOKEN. Перевірте токен у @BotFather (команда /token) і змінну BOT_TOKEN.")
        return 2
    except KeyboardInterrupt:
        pass
    log.info("Бот зупинено")
    return 0


if __name__ == "__main__":
    sys.exit(main())
