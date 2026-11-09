"""Службові команди, запуск і зупинка бота, точка входу python -m bot."""

import asyncio

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.methods import DeleteWebhook, GetMe, GetUpdates, SetMyCommands

from bot import __main__ as entry
from bot import app as app_module
from bot.app import build_app, run, wait_for_telegram
from bot.config import Settings
from bot.content import load_content
from tests.fake_telegram import FakeTelegram, tg_user
from tests.harness import REFERENCE_CONTENT_PATH, TOKEN

ADMIN = 900


# ----------------------------------------------------------------------------- /myid і /stats


async def test_myid_in_private_and_group_chat(harness):
    await harness.send("/myid", tg_user(5))
    [reply] = harness.telegram.sent(5)
    assert "Ваш id: <code>5</code>" in reply.text and "MANAGER_CHAT_ID" in reply.text
    harness.telegram.clear()
    await harness.send("/myid", tg_user(5), chat_type="group", chat_id=-100777)
    [reply] = harness.telegram.sent(-100777)
    assert "Id цього чату: <code>-100777</code>" in reply.text


async def test_stats_for_admin(make):
    harness = await make(admin_ids={ADMIN})
    await harness.send("/start instagram", tg_user(1))
    await harness.send("/start", tg_user(2))
    await harness.press("pkg:vip", tg_user(1))
    await harness.press("pay:vip", tg_user(1))
    harness.telegram.clear()

    await harness.send("/stats", tg_user(ADMIN, "Адмін"))
    [reply] = harness.telegram.sent(ADMIN)
    assert "📊 <b>Статистика</b>" in reply.text
    assert "Користувачів усього: 2" in reply.text
    assert "• VIP: 1 / 1 / 1" in reply.text and "• Старт: 0 / 0 / 0" in reply.text
    assert "• instagram: 1" in reply.text
    assert await harness.storage.get_user(ADMIN) is None  # адміністратор не потрапив у воронку


async def test_stats_is_hidden_from_regular_users(make):
    harness = await make(admin_ids={ADMIN})
    await harness.send("/start")
    harness.telegram.clear()
    await harness.send("/stats")
    [reply] = harness.telegram.sent(1)
    assert "Статистика" not in reply.text
    assert reply.text.startswith("Я автоматичний помічник")  # як на будь-яке інше повідомлення


# ----------------------------------------------------------------------------- запуск і зупинка


@pytest.fixture
def no_wait(monkeypatch):
    delays = []

    async def instant(seconds):
        delays.append(seconds)

    monkeypatch.setattr(app_module, "_sleep", instant)
    return delays


async def test_wait_for_telegram_retries_until_available(no_wait):
    telegram = FakeTelegram()
    for _ in range(2):
        telegram.fail_next(GetMe, lambda m: TelegramNetworkError(method=m, message="Cannot connect"))
    app = await _app(telegram)
    try:
        me = await wait_for_telegram(app.bot)
        assert me.username == "dlya_rishuchyh_bot"
        assert no_wait == [5.0, 10.0]
    finally:
        await app.close()


async def test_wrong_token_stops_immediately(no_wait):
    telegram = FakeTelegram()
    telegram.fail_next(GetMe, lambda m: TelegramUnauthorizedError(method=m, message="Unauthorized"))
    app = await _app(telegram)
    try:
        with pytest.raises(TelegramUnauthorizedError):
            await wait_for_telegram(app.bot)
        assert no_wait == []
    finally:
        await app.close()


async def test_run_sets_up_bot_and_stops_cleanly(tmp_path):
    telegram = FakeTelegram()
    app = await _app(telegram, tmp_path / "bot.db")
    task = asyncio.create_task(run(app, handle_signals=False))
    for _ in range(200):
        if telegram.calls(GetUpdates):
            break
        await asyncio.sleep(0.01)
    assert telegram.calls(GetUpdates), "бот не почав отримувати оновлення"
    [commands] = telegram.calls(SetMyCommands)
    assert [(c.command, c.description) for c in commands.commands] == [("start", "Отримати пропозицію для рішучих")]
    assert telegram.calls(DeleteWebhook)[0].drop_pending_updates is False  # повідомлення, надіслані під час простою, не губляться
    assert app.scheduler._tasks  # планувальник працює

    await app.dp.stop_polling()
    await asyncio.wait_for(task, timeout=5)
    assert not app.scheduler._tasks


async def _app(telegram, db_path=None):
    import tempfile
    from pathlib import Path

    path = db_path or Path(tempfile.mkdtemp()) / "bot.db"
    settings = Settings(bot_token=TOKEN, db_path=path, scheduler_tick=0.05)
    return await build_app(settings, load_content(REFERENCE_CONTENT_PATH), session=telegram, background_rate=0)


# ----------------------------------------------------------------------------- python -m bot


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.setattr(entry, "load_dotenv", lambda *a, **k: None)  # не читати справжній .env
    for name in ("BOT_TOKEN", "DB_PATH", "CONTENT_PATH", "MANAGER_CHAT_ID", "ADMIN_IDS", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# Логи бот пише в stdout (так їх бачить хостинг), тому перевіряємо stdout.


def test_main_without_token_explains_and_exits(clean_env, capsys):
    assert entry.main() == 2
    assert "BOT_TOKEN не задано" in capsys.readouterr().out


def test_main_with_broken_content_explains_and_exits(clean_env, tmp_path, capsys):
    broken = tmp_path / "content.yaml"
    broken.write_text("settings: {}\n", encoding="utf-8")
    clean_env.setenv("BOT_TOKEN", TOKEN)
    clean_env.setenv("CONTENT_PATH", str(broken))
    clean_env.setenv("DB_PATH", str(tmp_path / "bot.db"))
    assert entry.main() == 2
    assert "Помилок у файлі контенту" in capsys.readouterr().out


def test_main_with_rejected_token(clean_env, tmp_path, capsys):
    clean_env.setenv("BOT_TOKEN", TOKEN)
    clean_env.setenv("DB_PATH", str(tmp_path / "bot.db"))

    async def rejected(settings, content):
        raise TelegramUnauthorizedError(method=GetMe(), message="Unauthorized")

    clean_env.setattr(entry, "_main", rejected)
    assert entry.main() == 2
    assert "не приймає BOT_TOKEN" in capsys.readouterr().out
