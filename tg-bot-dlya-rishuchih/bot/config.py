"""Налаштування зі змінних середовища: секрети і технічні параметри (тексти — у content.yaml)."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .paths import DEFAULT_CONTENT_PATH, PROJECT_ROOT, project_path

_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]+$")
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class SettingsError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass(frozen=True)
class Settings:
    bot_token: str
    db_path: Path
    content_path: Path = DEFAULT_CONTENT_PATH
    manager_chat_id: int | None = None
    admin_ids: frozenset[int] = frozenset()
    log_level: str = "INFO"
    telegram_api_url: str | None = None
    scheduler_tick: float = 15.0
    warnings: tuple[str, ...] = ()


def _is_within(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Читає змінні середовища. Усі помилки збираються одразу (SettingsError)."""
    env = os.environ if env is None else env

    def get(name: str) -> str:
        return (env.get(name) or "").strip()

    errors: list[str] = []
    warnings: list[str] = []

    token = get("BOT_TOKEN").strip("\"'")
    if not token:
        errors.append(
            "BOT_TOKEN не задано. Отримайте токен у @BotFather і вкажіть його у файлі .env (локально) "
            "або у Variables на хостингу."
        )
    elif not _TOKEN_RE.match(token):
        errors.append(
            "BOT_TOKEN має неправильний формат: він виглядає як 123456789:AAH… (цифри, двокрапка, літери). "
            "Скопіюйте його з @BotFather ще раз, без пробілів і лапок."
        )

    manager_chat_id: int | None = None
    if get("MANAGER_CHAT_ID"):
        try:
            manager_chat_id = int(get("MANAGER_CHAT_ID"))
        except ValueError:
            errors.append("MANAGER_CHAT_ID має бути числом — id з команди /myid, наприклад 123456789 або -1001234567890")

    admin_ids: set[int] = set()
    for part in re.split(r"[\s,;]+", get("ADMIN_IDS")):
        if not part:
            continue
        try:
            admin_ids.add(int(part))
        except ValueError:
            errors.append(f"ADMIN_IDS: «{part}» — не число. Вкажіть id через кому, наприклад 123456789,987654321")

    # База даних: DB_PATH → Volume на Railway → data/bot.db у папці проєкту
    on_railway = any(key.startswith("RAILWAY_") for key in env)
    volume = get("RAILWAY_VOLUME_MOUNT_PATH")
    if get("DB_PATH"):
        db_path = project_path(get("DB_PATH"))
    elif volume:
        db_path = Path(volume) / "bot.db"
    else:
        db_path = PROJECT_ROOT / "data" / "bot.db"
    if on_railway and not volume:
        warnings.append(
            "Бот працює на Railway БЕЗ постійного диска (Volume): база даних зникне при наступному деплої, і таймери "
            "почнуться заново. Додайте Volume з Mount Path /data (DEPLOY.md, крок 5)."
        )
    elif on_railway and not _is_within(db_path, Path(volume)):
        warnings.append(
            f"DB_PATH ({db_path}) не на постійному диску {volume} — дані зникнуть при наступному деплої. "
            f"Видаліть змінну DB_PATH або вкажіть шлях усередині {volume}."
        )

    content_path = project_path(get("CONTENT_PATH")) if get("CONTENT_PATH") else DEFAULT_CONTENT_PATH

    log_level = (get("LOG_LEVEL") or "INFO").upper()
    if log_level not in _LOG_LEVELS:
        warnings.append(f"LOG_LEVEL «{log_level}» невідомий — використовую INFO")
        log_level = "INFO"

    scheduler_tick = 15.0
    if get("SCHEDULER_TICK_SECONDS"):
        try:
            scheduler_tick = float(get("SCHEDULER_TICK_SECONDS"))
            if not 0.05 <= scheduler_tick <= 300:
                raise ValueError
        except ValueError:
            errors.append("SCHEDULER_TICK_SECONDS має бути числом від 0.05 до 300 (секунд)")

    if errors:
        raise SettingsError(errors)
    return Settings(
        bot_token=token,
        db_path=db_path,
        content_path=content_path,
        manager_chat_id=manager_chat_id,
        admin_ids=frozenset(admin_ids),
        log_level=log_level,
        telegram_api_url=get("TELEGRAM_API_URL") or None,
        scheduler_tick=scheduler_tick,
        warnings=tuple(warnings),
    )
