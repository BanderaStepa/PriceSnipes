# Прогрес

**Поточний стан:** етап 0 завершено.
**Наступний крок:** етап 1 — `content.yaml` і його валідація (`bot/content.py`).

| № | Етап | Статус | Перевірка |
|---|---|---|---|
| 0 | Архітектура і каркас | ✅ готово | залежності встановлюються в чисте середовище |
| 1 | Контент | ⏳ | |
| 2 | Час і тексти | ⏳ | |
| 3 | База даних | ⏳ | |
| 4 | Логіка бота | ⏳ | |
| 5 | Планувальник | ⏳ | |
| 6 | Запуск і сервісні функції | ⏳ | |
| 7 | Хостинг | ⏳ | |
| 8 | Фінал | ⏳ | |

## Журнал

### 2026-11-08 — Етап 0: архітектура і каркас
- Визначено архітектуру, технології, БД і хостинг (`ARCHITECTURE.md`): Python 3.12+, aiogram 3, long polling,
  SQLite на постійному диску, Railway (Hobby) + Volume, Docker. Альтернатива — VPS з docker compose.
- Перевірено актуальний стан Railway (листопад 2026): `railway.toml` (Config as Code) для нових сервісів більше не
  приймається, тому налаштування задаються в панелі, а збірка — через `Dockerfile`. Volume змонтований у сервіс,
  Railway передає `RAILWAY_VOLUME_MOUNT_PATH`; дві копії сервісу з Volume одночасно не запускаються.
- Розписано етапи (`DEVELOPMENT_PLAN.md`), відкриті задачі (`TODO.md`), правила для наступних сесій (`CLAUDE.md`).
- Каркас: `requirements.txt` (aiogram 3.31.0, aiosqlite 0.22.1, PyYAML 6.0.3, python-dotenv 1.2.4, tzdata 2026.6),
  `requirements-dev.txt` (pytest, pytest-asyncio), `pytest.ini`, `.gitignore`, `.env.example`.
- Проєкт лежить у папці `tg-bot-dlya-rishuchih/` репозиторію; це відповідник папки
  `C:\Users\aimas\OneDrive\Desktop\TG BOT DLYA RISHUCHIH` (див. README).
- Перевірка: `pip install -r requirements-dev.txt` у чисте віртуальне середовище — успішно.
