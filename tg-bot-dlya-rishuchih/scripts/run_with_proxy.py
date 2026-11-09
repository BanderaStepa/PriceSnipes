"""Запуск бота в мережі, де Telegram доступний лише через HTTP(S)-проксі (наприклад, хмарна сесія Claude).

    python scripts/run_with_proxy.py .

Проксі береться зі змінної HTTPS_PROXY, сертифікат проксі (якщо він підміняє TLS) — з SSL_CERT_FILE.
Перевірка TLS не вимикається. Решта — так само, як python -m bot (ті самі змінні BOT_TOKEN, DB_PATH тощо).
На комп'ютері і на хостингу цей файл не потрібен: там start_bot.bat / python -m bot.
"""
import asyncio
import logging
import os
import ssl
import sys
from pathlib import Path

PROJECT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent.parent).resolve()
sys.path.insert(0, str(PROJECT))

import certifi  # noqa: E402
from aiohttp import ClientSession, TCPConnector  # noqa: E402
from aiogram.client.session.aiohttp import AiohttpSession  # noqa: E402

from bot.__main__ import setup_logging  # noqa: E402
from bot.app import build_app, run  # noqa: E402
from bot.config import load_settings  # noqa: E402
from bot.content import load_content  # noqa: E402

log = logging.getLogger("bot")


class ProxySession(AiohttpSession):
    """Як звичайна сесія aiogram, але з проксі з HTTPS_PROXY і сертифікатами з SSL_CERT_FILE (якщо задано)."""

    async def create_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            cafile = os.environ.get("SSL_CERT_FILE") or certifi.where()
            context = ssl.create_default_context(cafile=cafile)
            self._session = ClientSession(connector=TCPConnector(ssl=context), trust_env=True)
        return self._session


async def main() -> None:
    settings = load_settings()
    content = load_content(settings.content_path)
    for warning in (*settings.warnings, *content.warnings):
        log.warning(warning)
    log.info("Контент: %s пакет(и), %s нагадування", len(content.packages), len(content.reminders))
    app = await build_app(settings, content, session=ProxySession())
    await run(app)


if __name__ == "__main__":
    setup_logging("INFO")
    asyncio.run(main())
