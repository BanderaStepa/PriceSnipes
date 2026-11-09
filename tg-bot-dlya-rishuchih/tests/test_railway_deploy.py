"""scripts/railway_deploy.py: що саме потрапляє на Railway і як іде запит (без мережі)."""

import importlib.util
import io
import json
import subprocess
import tarfile
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "railway_deploy.py"


@pytest.fixture
def deploy():
    spec = importlib.util.spec_from_file_location("railway_deploy", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def git(cwd, *args):
    subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.email=test@example.com", "-c", "user.name=test", *args],
        check=True,
        capture_output=True,
    )


def test_archive_has_only_committed_files_of_the_bot_folder(deploy, tmp_path):
    repo = tmp_path / "repo"
    folder = repo / "tg-bot"
    (folder / "bot").mkdir(parents=True)
    (repo / "other.txt").write_text("інший проєкт у тому ж репозиторії", encoding="utf-8")
    (folder / "Dockerfile").write_text("FROM python:3.13-slim\n", encoding="utf-8")
    (folder / "bot" / "__main__.py").write_text("print('бот')\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    (folder / ".env").write_text("BOT_TOKEN=secret\n", encoding="utf-8")  # не закомічено — на Railway не потрапить
    (folder / "Dockerfile").write_text("FROM змінено\n", encoding="utf-8")  # зміна без коміту — теж

    with tarfile.open(fileobj=io.BytesIO(deploy.archive(folder)), mode="r:gz") as tar:
        files = {m.name: tar.extractfile(m).read().decode() for m in tar.getmembers() if m.isfile()}
    assert files == {"./Dockerfile": "FROM python:3.13-slim\n", "./bot/__main__.py": "print('бот')\n"}
    assert sorted(deploy.uncommitted(folder)) == [" M tg-bot/Dockerfile", "?? tg-bot/.env"]


def test_token_header_only_when_variable_is_set(deploy, monkeypatch):
    monkeypatch.delenv("RAILWAY_API_TOKEN", raising=False)
    assert "Authorization" not in deploy.headers("application/json")  # у хмарній сесії заголовок додає проксі
    monkeypatch.setenv("RAILWAY_API_TOKEN", "abc")
    assert deploy.headers("application/json")["Authorization"] == "Bearer abc"


def test_upload_posts_archive_to_the_bot_service(deploy, monkeypatch):
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(request)
        return FakeResponse(json.dumps({"deploymentId": "d1"}).encode())

    monkeypatch.setattr(deploy.urllib.request, "urlopen", fake_urlopen)
    assert deploy.upload(b"tar.gz", "git abc123 Етап 16") == {"deploymentId": "d1"}
    [request] = sent
    url = urllib.parse.urlsplit(request.full_url)
    assert url.netloc == "backboard.railway.com"
    assert url.path == f"/project/{deploy.PROJECT_ID}/environment/{deploy.ENVIRONMENT_ID}/up"
    assert urllib.parse.parse_qs(url.query) == {"serviceId": [deploy.SERVICE_ID], "message": ["git abc123 Етап 16"]}
    assert (request.get_method(), request.data) == ("POST", b"tar.gz")
    assert request.get_header("Content-type") == "application/gzip"


def test_rejected_token_is_explained(deploy, monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(deploy.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(deploy.DeployError, match="Railway не прийняв токен"):
        deploy.gql("{ me { id } }")


def test_wait_prints_each_status_once_and_stops_on_final(deploy, monkeypatch, capsys):
    statuses = iter(["INITIALIZING", "BUILDING", "BUILDING", "DEPLOYING", "SUCCESS"])
    monkeypatch.setattr(deploy, "deployment_status", lambda _id: next(statuses))
    ticks = iter(range(0, 1000, 5))
    assert deploy.wait("d1", clock=lambda: next(ticks), sleep=lambda _s: None) == "SUCCESS"
    printed = [line.split()[-1] for line in capsys.readouterr().out.splitlines()]
    assert printed == ["INITIALIZING", "BUILDING", "DEPLOYING", "SUCCESS"]


def test_failed_build_shows_build_log_and_keeps_old_version(deploy, monkeypatch, capsys):
    monkeypatch.setattr(deploy, "uncommitted", lambda: [])
    monkeypatch.setattr(deploy, "commit_title", lambda: "abc123 Етап 16")
    monkeypatch.setattr(deploy, "archive", lambda: b"x" * 4096)
    monkeypatch.setattr(deploy, "upload", lambda body, message: {"deploymentId": "d1"})
    monkeypatch.setattr(deploy, "wait", lambda _id: "FAILED")
    monkeypatch.setattr(
        deploy, "logs", lambda _id, build=False, limit=50: ["messages.offer: текст порожній"] if build else []
    )
    assert deploy.deploy() == 1
    out = capsys.readouterr().out
    assert "Попередня версія бота працює далі" in out
    assert "messages.offer: текст порожній" in out
