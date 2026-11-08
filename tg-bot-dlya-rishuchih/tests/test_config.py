from pathlib import Path

import pytest

from bot.config import SettingsError, load_settings
from bot.paths import DEFAULT_CONTENT_PATH, PROJECT_ROOT

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


def errors_of(env) -> str:
    with pytest.raises(SettingsError) as info:
        load_settings(env)
    return "\n".join(info.value.errors)


def test_minimal_local_settings():
    settings = load_settings({"BOT_TOKEN": TOKEN})
    assert settings.bot_token == TOKEN
    assert settings.db_path == PROJECT_ROOT / "data" / "bot.db"
    assert settings.content_path == DEFAULT_CONTENT_PATH
    assert settings.manager_chat_id is None and settings.admin_ids == frozenset()
    assert settings.scheduler_tick == 15.0 and settings.warnings == ()


def test_token_quotes_and_spaces_are_tolerated():
    assert load_settings({"BOT_TOKEN": f'  "{TOKEN}" '}).bot_token == TOKEN


def test_missing_or_malformed_token():
    assert "BOT_TOKEN не задано" in errors_of({})
    assert "неправильний формат" in errors_of({"BOT_TOKEN": "мій токен"})


def test_manager_and_admins():
    settings = load_settings({"BOT_TOKEN": TOKEN, "MANAGER_CHAT_ID": "-1001234567890", "ADMIN_IDS": "1, 2;3 4"})
    assert settings.manager_chat_id == -1001234567890
    assert settings.admin_ids == {1, 2, 3, 4}


def test_bad_numbers_are_explained():
    errors = errors_of({"BOT_TOKEN": TOKEN, "MANAGER_CHAT_ID": "@manager", "ADMIN_IDS": "1,abc",
                        "SCHEDULER_TICK_SECONDS": "0"})
    assert "MANAGER_CHAT_ID має бути числом" in errors
    assert "ADMIN_IDS: «abc» — не число" in errors
    assert "SCHEDULER_TICK_SECONDS" in errors


def test_relative_paths_are_inside_project():
    settings = load_settings({"BOT_TOKEN": TOKEN, "DB_PATH": "state/my.db", "CONTENT_PATH": "other.yaml"})
    assert settings.db_path == PROJECT_ROOT / "state" / "my.db"
    assert settings.content_path == PROJECT_ROOT / "other.yaml"


def test_railway_volume_is_used_automatically():
    settings = load_settings({"BOT_TOKEN": TOKEN, "RAILWAY_ENVIRONMENT_NAME": "production",
                              "RAILWAY_VOLUME_MOUNT_PATH": "/data"})
    assert settings.db_path == Path("/data/bot.db")
    assert settings.warnings == ()


def test_railway_without_volume_warns():
    settings = load_settings({"BOT_TOKEN": TOKEN, "RAILWAY_ENVIRONMENT_NAME": "production"})
    assert len(settings.warnings) == 1 and "БЕЗ постійного диска" in settings.warnings[0]


def test_railway_db_outside_volume_warns():
    settings = load_settings({"BOT_TOKEN": TOKEN, "RAILWAY_ENVIRONMENT_NAME": "production",
                              "RAILWAY_VOLUME_MOUNT_PATH": "/data", "DB_PATH": "/app/bot.db"})
    assert len(settings.warnings) == 1 and "не на постійному диску" in settings.warnings[0]


def test_unknown_log_level_falls_back_to_info():
    settings = load_settings({"BOT_TOKEN": TOKEN, "LOG_LEVEL": "loud"})
    assert settings.log_level == "INFO" and settings.warnings
