"""Оновлення бота на Railway: залити закомічений код і подивитися журнал.

    python scripts/railway_deploy.py             — залити код, дочекатися запуску, показати журнал бота
    python scripts/railway_deploy.py --status    — стан останнього деплою
    python scripts/railway_deploy.py --logs 100  — останні рядки журналу бота

Заливається лише закомічене (git HEAD цієї папки): незакомічені зміни, .env і локальна база на Railway
не потрапляють. Дані користувачів лежать на постійному диску (Volume /data) і під час оновлення не змінюються.

Токен Railway:
* у хмарній сесії Claude — секрет середовища для backboard.railway.com: його підставляє проксі, у коді його немає;
* на комп'ютері — змінна RAILWAY_API_TOKEN (railway.com/account/tokens → Create Token, Workspace: No workspace).

Робить те саме, що `railway up` з офіційної програми Railway CLI, але без її встановлення.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

# Проєкт «dlya-rishuchih-bot» на Railway (створено 2026-11-09). Ідентифікатори не секретні: без токена вони нічого не дають.
PROJECT_ID = "f78d3015-2822-44b7-8bed-44eb8e11ce9d"
ENVIRONMENT_ID = "103a084a-1407-47cc-866a-70966f03a85a"  # production
SERVICE_ID = "dfccfb46-1083-4ba4-8cf8-b9fa7ee2ee54"  # bot

BACKBOARD = "https://backboard.railway.com"
FINAL_STATUSES = frozenset({"SUCCESS", "FAILED", "CRASHED", "REMOVED", "SKIPPED"})


class DeployError(Exception):
    """Помилка з поясненням українською — виводиться без трасування."""


def headers(content_type: str) -> dict[str, str]:
    result = {"Content-Type": content_type, "User-Agent": "rishuchi-bot-deploy/1.0"}
    token = os.environ.get("RAILWAY_API_TOKEN", "").strip()
    if token:  # у хмарній сесії змінної немає: заголовок додає проксі
        result["Authorization"] = f"Bearer {token}"
    return result


def _request(url: str, data: bytes, content_type: str, timeout: float) -> dict:
    request = urllib.request.Request(url, data=data, method="POST", headers=headers(content_type))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        details = error.read()[:300].decode(errors="replace")
        if error.code in (401, 403):
            raise DeployError(
                f"Railway не прийняв токен (HTTP {error.code}). Перевірте RAILWAY_API_TOKEN або секрет середовища "
                f"для backboard.railway.com (токен міг бути видалений на railway.com/account/tokens)."
            ) from None
        raise DeployError(f"Railway відповів HTTP {error.code}: {details}") from None
    except urllib.error.URLError as error:
        raise DeployError(f"Немає з'єднання з Railway: {error.reason}") from None


def gql(query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    data = _request(f"{BACKBOARD}/graphql/v2", body, "application/json", timeout=60)
    if data.get("errors"):
        raise DeployError("Railway повернув помилку: " + "; ".join(e.get("message", "?") for e in data["errors"]))
    return data["data"]


def _git(project: Path, *args: str) -> bytes:
    try:
        return subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True).stdout
    except FileNotFoundError:
        raise DeployError("Не знайдено git — встановіть його (git-scm.com)") from None
    except subprocess.CalledProcessError as error:
        raise DeployError(f"git {' '.join(args)}: {error.stderr.decode(errors='replace').strip()}") from None


def archive(project: Path = PROJECT) -> bytes:
    """Закомічені файли папки бота (git HEAD) у tar.gz, шляхи як у `railway up`: ./Dockerfile, ./bot/…"""
    root = Path(_git(project, "rev-parse", "--show-toplevel").decode().strip())
    prefix = _git(project, "rev-parse", "--show-prefix").decode().strip().rstrip("/")
    tree = f"HEAD:{prefix}" if prefix else "HEAD"
    # Запускати з кореня репозиторію: у підпапці git archive ще раз обрізає вміст до неї, і архів виходить порожнім
    return _git(root, "archive", "--format=tar.gz", "--prefix=./", tree)


def uncommitted(project: Path = PROJECT) -> list[str]:
    return _git(project, "status", "--porcelain", "--", ".").decode().splitlines()


def commit_title(project: Path = PROJECT) -> str:
    return _git(project, "log", "-1", "--format=%h %s").decode().strip()


def upload(body: bytes, message: str) -> dict:
    query = urllib.parse.urlencode({"serviceId": SERVICE_ID, "message": message})
    url = f"{BACKBOARD}/project/{PROJECT_ID}/environment/{ENVIRONMENT_ID}/up?{query}"
    return _request(url, body, "application/gzip", timeout=300)


def deployment_status(deployment_id: str) -> str:
    return gql("query($id: String!) { deployment(id: $id) { status } }", {"id": deployment_id})["deployment"]["status"]


def wait(deployment_id: str, timeout: float = 600, poll: float = 5, clock=time.monotonic, sleep=time.sleep) -> str:
    """Чекати, доки деплой завершиться; кожну зміну стану вивести один раз."""
    start, last = clock(), None
    while clock() - start < timeout:
        status = deployment_status(deployment_id)
        if status != last:
            print(f"  [{int(clock() - start):>3} с] {status}", flush=True)
            last = status
        if status in FINAL_STATUSES:
            return status
        sleep(poll)
    return "TIMEOUT"


def logs(deployment_id: str, build: bool = False, limit: int = 50) -> list[str]:
    field = "buildLogs" if build else "deploymentLogs"
    query = f"query($id: String!, $limit: Int) {{ {field}(deploymentId: $id, limit: $limit) {{ message }} }}"
    return [row["message"] for row in gql(query, {"id": deployment_id, "limit": limit})[field]]


def latest_deployment() -> dict | None:
    query = """query($input: DeploymentListInput!) {
      deployments(input: $input, first: 1) { edges { node { id status createdAt } } } }"""
    variables = {"input": {"projectId": PROJECT_ID, "environmentId": ENVIRONMENT_ID, "serviceId": SERVICE_ID}}
    edges = gql(query, variables)["deployments"]["edges"]
    return edges[0]["node"] if edges else None


def deploy() -> int:
    changes = uncommitted()
    if changes:
        print("⚠️  Є незакомічені зміни — на Railway потрапить лише закомічене:")
        print("\n".join("    " + line for line in changes[:10]))
    title = commit_title()
    body = archive()
    print(f"Заливаю на Railway: {title} ({len(body) // 1024} КБ)")
    deployment_id = upload(body, f"git {title}")["deploymentId"]
    status = wait(deployment_id)
    if status != "SUCCESS":
        print(f"❌ Деплой не вдався ({status}). Попередня версія бота працює далі. Кінець журналу збірки:")
        print("\n".join(logs(deployment_id, build=True, limit=200)[-40:]))
        return 1
    time.sleep(10)  # дати боту стартувати, щоб у журналі був «Run polling»
    print("✅ Готово. Журнал бота:")
    print("\n".join(logs(deployment_id, limit=30)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Оновлення бота на Railway")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="стан останнього деплою")
    group.add_argument("--logs", type=int, nargs="?", const=50, metavar="N", help="останні N рядків журналу бота")
    args = parser.parse_args(argv)
    try:
        if args.status or args.logs:
            latest = latest_deployment()
            if latest is None:
                print("Деплоїв ще немає")
                return 1
            print(f"Останній деплой: {latest['status']}, {latest['createdAt']} (id {latest['id']})")
            if args.logs:
                print("\n".join(logs(latest["id"], limit=args.logs)))
            return 0
        return deploy()
    except DeployError as error:
        print(f"❌ {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
