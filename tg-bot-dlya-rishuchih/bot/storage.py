"""SQLite: користувачі, журнал нагадувань, події.

Усі часові мітки — UTC, цілі unix-секунди. started_at і deadline_at записуються лише під час першого /start
і більше ніколи не змінюються: повторний /start не перезапускає таймер.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Міграції: версія → SQL. Нові зміни схеми додаються новою версією, старі не редагуються.
_MIGRATIONS: dict[int, str] = {
    1: """
        CREATE TABLE users (
            user_id          INTEGER PRIMARY KEY,
            chat_id          INTEGER NOT NULL,
            first_name       TEXT    NOT NULL DEFAULT '',
            last_name        TEXT,
            username         TEXT,
            language_code    TEXT,
            source           TEXT,
            started_at       INTEGER NOT NULL,
            deadline_at      INTEGER NOT NULL,
            offer_message_id INTEGER,
            expiry_done      INTEGER NOT NULL DEFAULT 0,
            blocked_at       INTEGER,
            last_seen_at     INTEGER NOT NULL
        );
        CREATE INDEX idx_users_deadline ON users (deadline_at);

        CREATE TABLE reminder_log (
            user_id     INTEGER NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
            reminder_id TEXT    NOT NULL,
            status      TEXT    NOT NULL,
            at          INTEGER NOT NULL,
            PRIMARY KEY (user_id, reminder_id)
        );

        CREATE TABLE events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            kind       TEXT    NOT NULL,
            package_id TEXT,
            at         INTEGER NOT NULL
        );
        CREATE INDEX idx_events_kind ON events (kind, package_id);
    """,
}

# Типи подій для статистики
EVENT_START = "start"  # перший /start
EVENT_RESTART = "restart"  # повторний /start
EVENT_PACKAGES = "packages"  # відкрив список пакетів
EVENT_PACKAGE = "package"  # відкрив опис пакета
EVENT_PAYMENT_LINK = "payment_link"  # отримав посилання на оплату

# Статуси в журналі нагадувань
REMINDER_SENT = "sent"
REMINDER_SKIPPED = "skipped"  # пропущено: настав час пізнішого нагадування (наприклад, після простою сервера)
REMINDER_FAILED = "failed"  # не вдалося надіслати і повторювати немає сенсу (заблокував бота тощо)


@dataclass(frozen=True)
class Profile:
    """Дані з Telegram, які оновлюються при кожній взаємодії."""

    user_id: int
    chat_id: int
    first_name: str = ""
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None


@dataclass(frozen=True)
class UserRecord:
    user_id: int
    chat_id: int
    first_name: str
    last_name: str | None
    username: str | None
    language_code: str | None
    source: str | None
    started_at: int
    deadline_at: int
    offer_message_id: int | None
    expiry_done: bool
    blocked_at: int | None
    last_seen_at: int

    @property
    def is_blocked(self) -> bool:
        return self.blocked_at is not None

    def is_expired(self, now: int) -> bool:
        return now >= self.deadline_at


@dataclass(frozen=True)
class Stats:
    users_total: int
    users_last_24h: int
    active: int
    expired: int
    blocked: int
    reminders_sent: int
    package_views: dict[str, int] = field(default_factory=dict)
    # id пакета → (скільки разів запитали посилання, скільки різних людей)
    payment_links: dict[str, tuple[int, int]] = field(default_factory=dict)
    sources: list[tuple[str, int]] = field(default_factory=list)


def _user(row: aiosqlite.Row) -> UserRecord:
    return UserRecord(
        user_id=row["user_id"],
        chat_id=row["chat_id"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        username=row["username"],
        language_code=row["language_code"],
        source=row["source"],
        started_at=row["started_at"],
        deadline_at=row["deadline_at"],
        offer_message_id=row["offer_message_id"],
        expiry_done=bool(row["expiry_done"]),
        blocked_at=row["blocked_at"],
        last_seen_at=row["last_seen_at"],
    )


class Storage:
    def __init__(self, conn: aiosqlite.Connection, path: Path) -> None:
        self._conn = conn
        self.path = path

    @classmethod
    async def open(cls, path: str | Path) -> Storage:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path, isolation_level=None)  # autocommit: кожен запит зберігається одразу
        conn.row_factory = aiosqlite.Row
        try:
            await conn.execute("PRAGMA journal_mode = WAL")
            await conn.execute("PRAGMA synchronous = NORMAL")
            await conn.execute("PRAGMA foreign_keys = ON")
            await conn.execute("PRAGMA busy_timeout = 5000")
            storage = cls(conn, path)
            await storage._migrate()
        except BaseException:
            await conn.close()
            raise
        return storage

    async def close(self) -> None:
        await self._conn.close()

    async def _migrate(self) -> None:
        version = await self._scalar("PRAGMA user_version")
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"База даних {self.path} створена новішою версією бота (схема {version}, ця версія знає {SCHEMA_VERSION})"
            )
        for target in range(version + 1, SCHEMA_VERSION + 1):
            log.info("База даних: застосовую міграцію до версії %s", target)
            await self._conn.executescript(f"BEGIN;\n{_MIGRATIONS[target]}\nPRAGMA user_version = {target};\nCOMMIT;")

    # ------------------------------------------------------------------ допоміжне

    async def _scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        async with self._conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return row[0] if row is not None else None

    async def _users(self, sql: str, params: tuple[Any, ...] = ()) -> list[UserRecord]:
        async with self._conn.execute(sql, params) as cur:
            return [_user(row) for row in await cur.fetchall()]

    # ------------------------------------------------------------------ користувачі

    async def register_start(
        self, profile: Profile, *, now: int, duration: int, source: str | None = None
    ) -> tuple[UserRecord, bool]:
        """Перший /start створює користувача і таймер; повторний лише оновлює профіль.

        Повертає (користувач, чи це перший запуск).
        """
        async with self._conn.execute(
            """
            INSERT INTO users (user_id, chat_id, first_name, last_name, username, language_code, source,
                               started_at, deadline_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (
                profile.user_id, profile.chat_id, profile.first_name, profile.last_name, profile.username,
                profile.language_code, (source or None) and source[:64], now, now + duration, now,
            ),
        ) as cur:
            created = cur.rowcount == 1
        if not created:
            await self.touch(profile, now=now)
        user = await self.get_user(profile.user_id)
        assert user is not None
        return user, created

    async def touch(self, profile: Profile, *, now: int) -> None:
        """Оновлює профіль і час останньої взаємодії; знімає позначку «заблокував бота»."""
        await self._conn.execute(
            """
            UPDATE users SET chat_id = ?, first_name = ?, last_name = ?, username = ?, language_code = ?,
                             blocked_at = NULL, last_seen_at = ?
            WHERE user_id = ?
            """,
            (profile.chat_id, profile.first_name, profile.last_name, profile.username, profile.language_code,
             now, profile.user_id),
        )

    async def get_user(self, user_id: int) -> UserRecord | None:
        users = await self._users("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return users[0] if users else None

    async def set_offer_message(self, user_id: int, message_id: int) -> None:
        await self._conn.execute("UPDATE users SET offer_message_id = ? WHERE user_id = ?", (message_id, user_id))

    async def clear_offer_message(self, user_id: int, message_id: int) -> None:
        """Забуває повідомлення з офером (лише якщо це те саме повідомлення — новіше не чіпаємо)."""
        await self._conn.execute(
            "UPDATE users SET offer_message_id = NULL WHERE user_id = ? AND offer_message_id = ?",
            (user_id, message_id),
        )

    async def mark_blocked(self, user_id: int, *, now: int) -> None:
        await self._conn.execute(
            "UPDATE users SET blocked_at = ? WHERE user_id = ? AND blocked_at IS NULL", (now, user_id)
        )

    # ------------------------------------------------------------------ для планувальника

    async def active_users(self, now: int) -> list[UserRecord]:
        """Пропозиція ще діє і бот не заблоковано."""
        return await self._users(
            "SELECT * FROM users WHERE deadline_at > ? AND blocked_at IS NULL ORDER BY deadline_at", (now,)
        )

    async def reminder_log(self, now: int) -> dict[int, set[str]]:
        """Які нагадування вже оброблено (надіслано/пропущено) — лише для користувачів з активною пропозицією."""
        result: dict[int, set[str]] = {}
        async with self._conn.execute(
            """
            SELECT r.user_id, r.reminder_id FROM reminder_log AS r
            JOIN users AS u ON u.user_id = r.user_id
            WHERE u.deadline_at > ? AND u.blocked_at IS NULL
            """,
            (now,),
        ) as cur:
            async for row in cur:
                result.setdefault(row[0], set()).add(row[1])
        return result

    async def log_reminder(self, user_id: int, reminder_id: str, status: str, *, now: int) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO reminder_log (user_id, reminder_id, status, at) VALUES (?, ?, ?, ?)",
            (user_id, reminder_id, status, now),
        )

    async def pending_expiry(self, now: int) -> list[UserRecord]:
        """Дедлайн минув, а завершення ще не оброблено."""
        return await self._users(
            "SELECT * FROM users WHERE deadline_at <= ? AND expiry_done = 0 ORDER BY deadline_at", (now,)
        )

    async def mark_expiry_done(self, user_id: int) -> None:
        await self._conn.execute("UPDATE users SET expiry_done = 1 WHERE user_id = ?", (user_id,))

    async def live_offers(self, now: int) -> list[UserRecord]:
        """Користувачі, у яких є повідомлення з офером, що має живий таймер."""
        return await self._users(
            """
            SELECT * FROM users
            WHERE deadline_at > ? AND offer_message_id IS NOT NULL AND blocked_at IS NULL
            ORDER BY deadline_at
            """,
            (now,),
        )

    # ------------------------------------------------------------------ події і статистика

    async def add_event(self, user_id: int, kind: str, *, now: int, package_id: str | None = None) -> None:
        await self._conn.execute(
            "INSERT INTO events (user_id, kind, package_id, at) VALUES (?, ?, ?, ?)", (user_id, kind, package_id, now)
        )

    async def stats(self, now: int) -> Stats:
        async def grouped(sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
            async with self._conn.execute(sql, params) as cur:
                return list(await cur.fetchall())

        views = await grouped(
            "SELECT package_id, COUNT(*) FROM events WHERE kind = ? GROUP BY package_id", (EVENT_PACKAGE,)
        )
        links = await grouped(
            "SELECT package_id, COUNT(*), COUNT(DISTINCT user_id) FROM events WHERE kind = ? GROUP BY package_id",
            (EVENT_PAYMENT_LINK,),
        )
        sources = await grouped(
            "SELECT source, COUNT(*) FROM users WHERE source IS NOT NULL GROUP BY source ORDER BY 2 DESC, 1 LIMIT 10"
        )
        return Stats(
            users_total=await self._scalar("SELECT COUNT(*) FROM users"),
            users_last_24h=await self._scalar("SELECT COUNT(*) FROM users WHERE started_at > ?", (now - 86400,)),
            active=await self._scalar("SELECT COUNT(*) FROM users WHERE deadline_at > ?", (now,)),
            expired=await self._scalar("SELECT COUNT(*) FROM users WHERE deadline_at <= ?", (now,)),
            blocked=await self._scalar("SELECT COUNT(*) FROM users WHERE blocked_at IS NOT NULL"),
            reminders_sent=await self._scalar(
                "SELECT COUNT(*) FROM reminder_log WHERE status = ?", (REMINDER_SENT,)
            ),
            package_views={row[0]: row[1] for row in views},
            payment_links={row[0]: (row[1], row[2]) for row in links},
            sources=[(row[0], row[1]) for row in sources],
        )
