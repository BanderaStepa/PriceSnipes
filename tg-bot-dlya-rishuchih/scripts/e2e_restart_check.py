"""E2E-перевірка: справжній процес бота проти локального фейкового Telegram Bot API, із перезапуском процесу.

    python scripts/e2e_restart_check.py                       # бот як звичайний процес (python -m bot)
    python scripts/e2e_restart_check.py --docker rishuchi-bot # бот у Docker-контейнері, як на Railway:
                                                              # Volume у /data, кожен перезапуск — новий контейнер

Що відбувається (≈2 хв): пропозиція скорочується до 90 с, нагадування — через 20 с і за 30 с до кінця.
1. Запуск бота → /start → офер із таймером → пакети → опис → посилання на оплату.
2. Перше нагадування → бот зупиняється (SIGTERM) і запускається знову, як при перезапуску сервера.
3. Повторний /start не перезапускає таймер; друге нагадування приходить, перше не дублюється.
4. Через 90 с офер показує «час вийшов» без кнопок, кнопки відповідають «пропозиція завершилась».
Мережа не потрібна: бот звертається до сервера на 127.0.0.1 через TELEGRAM_API_URL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import yaml
from aiohttp import web

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

REFERENCE_CONTENT_PATH = PROJECT / "tests" / "fixtures" / "content.yaml"  # еталонний контент (пакети start/optimal/vip)

BOT = {"id": 424242, "is_bot": True, "first_name": "E2E", "username": "e2e_bot"}
USER = {"id": 1, "is_bot": False, "first_name": "Оля", "username": "olya", "language_code": "uk"}


@dataclass
class Call:
    at: float
    method: str
    params: dict
    result: object = None  # що відповів «Telegram» (для sendMessage — повідомлення з message_id)

    @property
    def text(self) -> str:
        return self.params.get("text", "")

    @property
    def markup(self) -> dict | None:
        raw = self.params.get("reply_markup")
        return json.loads(raw) if raw else None


class FakeBotAPI:
    """Мінімальний Bot API: getMe, getUpdates (довге опитування), sendMessage, editMessageText, решта → true."""

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.updates: list[dict] = []
        self._next_update = 1
        self._message_id = 100
        self._incoming = 1
        self._new_update = asyncio.Event()
        self._app = web.Application()
        self._app.router.add_route("*", "/bot{token}/{method}", self._handle)
        self._runner: web.AppRunner | None = None
        self.port = 0

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    # --- що «надсилає користувач»

    def push(self, update: dict) -> None:
        update["update_id"] = self._next_update
        self._next_update += 1
        self.updates.append(update)
        self._new_update.set()

    def user_message(self, text: str) -> None:
        self._incoming += 1
        entities = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text.startswith("/") else None
        message = {"message_id": self._incoming, "date": int(time.time()),
                   "chat": {"id": USER["id"], "type": "private", "first_name": USER["first_name"]},
                   "from": USER, "text": text}
        if entities:
            message["entities"] = entities
        self.push({"message": message})

    def press(self, data: str, message_id: int = 1) -> None:
        self.push({"callback_query": {
            "id": f"cb{self._next_update}", "from": USER, "chat_instance": "e2e", "data": data,
            "message": {"message_id": message_id, "date": int(time.time()),
                        "chat": {"id": USER["id"], "type": "private"}, "text": "…"},
        }})

    # --- очікування

    async def wait_for(self, check, *, since: float = 0.0, timeout: float = 30.0, what: str = "") -> Call:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for call in self.calls:
                if call.at >= since and check(call):
                    return call
            await asyncio.sleep(0.1)
        raise TimeoutError(f"не дочекалися: {what or check}")

    async def wait_message(self, fragment: str, *, since: float, timeout: float = 30.0) -> Call:
        return await self.wait_for(lambda c: c.method == "sendmessage" and fragment in c.text, since=since,
                                   timeout=timeout, what=f"повідомлення з «{fragment}»")

    # --- HTTP

    async def _handle(self, request: web.Request) -> web.Response:
        method = request.match_info["method"].lower()
        params = {k: v for k, v in (await request.post()).items() if isinstance(v, str)}
        params.update(request.query)
        call = Call(time.time(), method, params)
        self.calls.append(call)
        handler = getattr(self, f"_m_{method}", None)
        call.result = await handler(params) if handler else True
        return web.json_response({"ok": True, "result": call.result})

    async def _m_getme(self, params: dict) -> dict:
        return BOT

    async def _m_getupdates(self, params: dict) -> list[dict]:
        offset = int(params.get("offset") or 0)
        self.updates = [u for u in self.updates if u["update_id"] >= offset]
        if not self.updates:
            self._new_update.clear()
            try:
                await asyncio.wait_for(self._new_update.wait(), timeout=min(float(params.get("timeout") or 0), 2.0))
            except TimeoutError:
                pass
        return self.updates[:100]

    def _message(self, message_id: int, params: dict) -> dict:
        return {"message_id": message_id, "date": int(time.time()), "from": BOT,
                "chat": {"id": int(params["chat_id"]), "type": "private"}, "text": params.get("text", "")}

    async def _m_sendmessage(self, params: dict) -> dict:
        self._message_id += 1
        return self._message(self._message_id, params)

    async def _m_editmessagetext(self, params: dict) -> dict:
        return self._message(int(params["message_id"]), params)


class Checks:
    def __init__(self) -> None:
        self.failed = 0

    def __call__(self, ok: bool, name: str, detail: str = "") -> None:
        mark = "✅" if ok else "❌"
        print(f"{mark} {name}" + (f" — {detail}" if detail and not ok else ""), flush=True)
        self.failed += not ok


def buttons(call: Call) -> list[str]:
    markup = call.markup or {}
    return [b.get("callback_data") or b.get("url") for row in markup.get("inline_keyboard", []) for b in row]


async def start_bot(env: dict, log_file, docker_image: str | None, work: Path) -> asyncio.subprocess.Process:
    if docker_image is None:
        return await asyncio.create_subprocess_exec(
            sys.executable, "-m", "bot", cwd=str(PROJECT), env=env, stdout=log_file, stderr=asyncio.subprocess.STDOUT
        )
    # Як на Railway: Volume змонтовано в /data, Railway передає RAILWAY_VOLUME_MOUNT_PATH, DB_PATH не задано.
    args = ["docker", "run", "--rm", "--name", f"e2e-bot-{os.getpid()}", "--network", "host", "-v", f"{work}:/data",
            "-e", "RAILWAY_ENVIRONMENT_NAME=e2e", "-e", "RAILWAY_VOLUME_MOUNT_PATH=/data",
            "-e", "CONTENT_PATH=/data/content.yaml"]
    for key in ("BOT_TOKEN", "TELEGRAM_API_URL", "SCHEDULER_TICK_SECONDS", "LOG_LEVEL"):
        args += ["-e", f"{key}={env[key]}"]
    return await asyncio.create_subprocess_exec(*args, docker_image, stdout=log_file,
                                                stderr=asyncio.subprocess.STDOUT)


async def scenario(docker_image: str | None = None) -> int:
    work = Path(tempfile.mkdtemp(prefix="e2e-bot-"))
    raw = yaml.safe_load(REFERENCE_CONTENT_PATH.read_text(encoding="utf-8"))
    raw["settings"].update(offer_duration="90 с", timer_update_every="1 хв", silent_hours="")
    raw["reminders"] = [
        {"id": "e1", "after": "20 с", "text": "E2E-НАГАДУВАННЯ-1: залишилось {time_left}"},
        {"id": "e2", "before_end": "30 с", "text": "E2E-НАГАДУВАННЯ-2: залишилось {time_left}"},
    ]
    content_path = work / "content.yaml"
    content_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    db_path = work / "bot.db"
    log_path = work / "bot.log"

    api = FakeBotAPI()
    await api.start()
    env = {k: v for k, v in os.environ.items() if not k.startswith("RAILWAY_")}
    env.update(BOT_TOKEN="123456:E2E-TOKEN", TELEGRAM_API_URL=f"http://127.0.0.1:{api.port}", DB_PATH=str(db_path),
               CONTENT_PATH=str(content_path), SCHEDULER_TICK_SECONDS="1", PYTHONUNBUFFERED="1", LOG_LEVEL="INFO")
    check = Checks()
    log_file = log_path.open("wb")
    proc = None
    try:
        print(f"Фейковий Bot API: http://127.0.0.1:{api.port}, тимчасова папка: {work}", flush=True)
        proc = await start_bot(env, log_file, docker_image, work)
        await api.wait_for(lambda c: c.method == "getupdates", timeout=60, what="початку polling")
        check(True, "бот запустився і отримує оновлення (getMe → getUpdates)")

        t = time.time()
        api.user_message("/start")
        offer = await api.wait_message("Пропозиція для рішучих", since=t)
        check("Залишилось: <b>2 хв</b>" in offer.text, "офер із таймером", offer.text)
        check(buttons(offer) == ["packages"], "під офером кнопка «Обрати пакет»", str(buttons(offer)))
        with sqlite3.connect(db_path) as conn:
            started_at, deadline_at = conn.execute("SELECT started_at, deadline_at FROM users").fetchone()
        check(deadline_at - started_at == 90, "перший /start записав таймер у БД")

        t = time.time()
        api.press("packages")
        listing = await api.wait_message("Оберіть пакет", since=t)
        check(buttons(listing) == ["pkg:start", "pkg:optimal", "pkg:vip"], "список пакетів", str(buttons(listing)))
        api.press("pkg:vip")
        details = await api.wait_message("Пакет «VIP» — 7 990 грн", since=t)
        check(buttons(details) == ["pay:vip", "packages"], "опис пакета з кнопками навігації", str(buttons(details)))
        api.press("pay:vip")
        payment = await api.wait_message("https://example.com/pay/vip", since=t)
        check(buttons(payment) == ["https://example.com/pay/vip", "packages"], "посилання на оплату", str(buttons(payment)))

        await api.wait_message("E2E-НАГАДУВАННЯ-1", since=0, timeout=40)
        check(True, "перше нагадування прийшло")

        # ---------------------------------------------------------------- перезапуск
        proc.terminate()
        code = await asyncio.wait_for(proc.wait(), timeout=20)
        check(code == 0, "бот коректно зупинився за сигналом SIGTERM", f"код виходу {code}")
        t = time.time()
        proc = await start_bot(env, log_file, docker_image, work)
        await api.wait_for(lambda c: c.method == "getupdates", since=t, timeout=60,
                           what="polling після перезапуску")
        check(True, "бот запустився знову (новий процес, та сама БД)")

        # Чекаємо, поки від першого /start мине 35 с: тоді залишок (≈55 с) показується як «1 хв»,
        # а таймер, запущений заново, показав би «2 хв».
        await asyncio.sleep(max(0.0, started_at + 35 - time.time()))
        t = time.time()
        api.user_message("/start")
        offer2 = await api.wait_message("Пропозиція для рішучих", since=t)
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT started_at, deadline_at FROM users").fetchall()
        check(rows == [(started_at, deadline_at)], "повторний /start після перезапуску не перезапустив таймер", str(rows))
        check("Залишилось: <b>1 хв</b>" in offer2.text, "офер показує залишок, а не нові 90 с", offer2.text)

        await api.wait_message("E2E-НАГАДУВАННЯ-2", since=0, timeout=60)
        first = [c for c in api.calls if c.method == "sendmessage" and "E2E-НАГАДУВАННЯ-1" in c.text]
        check(len(first) == 1, "перше нагадування не повторилося після перезапуску", f"{len(first)} шт.")

        final = await api.wait_for(
            lambda c: c.method == "editmessagetext" and "Час вийшов" in c.text, timeout=60, what="завершення пропозиції"
        )
        check(final.params.get("message_id") == str(offer2.result["message_id"]),  # type: ignore[index]
              "завершено той самий офер, що й живий таймер", str(final.params.get("message_id")))
        check(not buttons(final), "після 24 год (тут — 90 с) кнопок під офером немає", str(buttons(final)))
        live = [c for c in api.calls if c.method == "editmessagetext" and "Залишилось" in c.text]
        check(bool(live), "живий таймер оновлювався до завершення", f"{len(live)} оновлень")

        t = time.time()
        api.press("packages")
        expired = await api.wait_message("уже завершилась", since=t)
        check(not buttons(expired), "кнопки старих повідомлень відповідають «пропозиція завершилась»")
        await asyncio.sleep(3)
        late = [c for c in api.calls if c.method == "sendmessage" and "E2E-НАГАДУВАННЯ" in c.text and c.at > deadline_at]
        check(not late, "після завершення нагадування не надсилаються")
        if docker_image:
            log_file.flush()
            log = log_path.read_text(encoding="utf-8", errors="replace")
            check("База даних: /data/bot.db" in log, "у контейнері БД сама опинилась на Volume (/data/bot.db)")
            check("БЕЗ постійного диска" not in log, "попередження про відсутній Volume немає")
    except Exception as e:  # noqa: BLE001 — звіт про будь-яку помилку
        check(False, "сценарій перервано", repr(e))
    finally:
        if proc and proc.returncode is None:
            proc.terminate()
            await proc.wait()
        if docker_image:
            cleanup = await asyncio.create_subprocess_exec("docker", "rm", "-f", f"e2e-bot-{os.getpid()}",
                                                           stdout=asyncio.subprocess.DEVNULL,
                                                           stderr=asyncio.subprocess.DEVNULL)
            await cleanup.wait()
        log_file.close()
        await api.stop()
    if check.failed:
        print(f"\nЖурнал бота ({log_path}):\n" + log_path.read_text(encoding="utf-8", errors="replace")[-4000:])
        print(f"\nНЕ ПРОЙДЕНО: {check.failed} перевірок")
        return 1
    shutil.rmtree(work, ignore_errors=True)
    print("\nУсі перевірки пройдено.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--docker", metavar="IMAGE", help="запускати бота з Docker-образу (docker build -t IMAGE .)")
    args = parser.parse_args()
    sys.exit(asyncio.run(scenario(args.docker)))
