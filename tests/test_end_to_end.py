"""Whole run against routed fixtures: every row read, report saved, email built and 'sent' to a fake SMTP."""
import json
import shutil
from email import message_from_bytes
from pathlib import Path

import pytest

from conftest import ROOT
from pricecheck import emailer, runner
from pricecheck.browser import Session
from test_sites_browser import routed_session

ROUTES = {
    "geizhals.de/?fs=RTX": "geizhals_search_gpu.html",
    "geizhals.de/?fs=32GB": "geizhals_search_ram.html",
    "geizhals.de/": "geizhals_product.html",
    "MainSearchProductCategory": "idealo_search.html",
    "idealo.de/": "idealo_product.html",
    "amazon.de/": "amazon_product.html",
}


@pytest.fixture
def repo(tmp_path):
    shutil.copy(ROOT / "config.json", tmp_path / "config.json")
    return tmp_path


def _factory(page, repo):
    holder = {}

    def make(run_dir):
        s = routed_session(page, run_dir, ROUTES)
        holder["s"] = s
        return s
    return make


class FakeSMTP:
    sent = []
    fail = False

    def __init__(self, host, port, context=None, timeout=None):
        assert (host, port) == ("smtp.gmail.com", 465)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, pw):
        if FakeSMTP.fail:
            raise OSError("535 bad credentials")
        assert pw == "abcdabcdabcdabcd"

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.fail = [], False
    monkeypatch.setattr(emailer.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setenv("GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd abcd abcd abcd")
    monkeypatch.setenv("MAIL_TO", "me@example.com")
    monkeypatch.delenv("PRICECHECK_STDOUT_LOG", raising=False)
    return FakeSMTP


def _only_run(repo):
    runs = [p for p in (repo / "runs").iterdir() if p.is_dir()]
    assert len(runs) == 1
    return runs[0]


def test_full_run_with_email(page, repo, fake_smtp):
    rc = runner.run([], root=repo, session_factory=_factory(page, repo))
    assert rc == 0
    run = _only_run(repo)
    data = json.loads((run / "results.json").read_text(encoding="utf-8"))
    res = data["results"]
    assert len(res) == 16
    assert all(r["status"] == "ok" for r in res.values()), {k: r["status"] for k, r in res.items()}
    assert res["7a"]["price"] == "50.51"
    assert res["6a"]["price"] == "519.90"
    assert res["F2"]["product"].startswith("Team Group T-Force")
    assert (run / "report.html").exists() and (run / "report.txt").exists()
    assert (run / "1.png").exists() and (run / "F3_offer.png").exists()
    assert (repo / "logs" / "run.log").read_text(encoding="utf-8").count("GET https://") >= 16

    assert len(fake_smtp.sent) == 1
    msg = fake_smtp.sent[0]
    assert msg["To"] == "me@example.com"
    assert msg["Subject"].startswith("White Build prices – ")
    names = [p.get_filename() for p in msg.iter_attachments()]
    assert "01_CPU.png" in names and "06c_TForce_idealo.png" in names and "F3_offer.png" in names
    assert "07a_Cooler_amazon.png" in names
    html = msg.get_body(("html",)).get_content()
    assert "Spec filter links (cheapest part with the required specs, any brand)" in html
    assert msg.get_body(("plain",)).get_content().startswith("White Build prices")


def test_email_failure_exit_code_2(page, repo, fake_smtp):
    fake_smtp.fail = True
    rc = runner.run(["--only", "7a"], root=repo, session_factory=_factory(page, repo))
    assert rc == 2
    run = _only_run(repo)
    assert (run / "report.html").exists()
    assert "sending the email failed" in (repo / "logs" / "run.log").read_text(encoding="utf-8")


def test_only_and_no_email(page, repo, fake_smtp):
    rc = runner.run(["--no-email", "--only", "2,6c,F3"], root=repo, session_factory=_factory(page, repo))
    assert rc == 0 and not fake_smtp.sent
    res = json.loads((_only_run(repo) / "results.json").read_text(encoding="utf-8"))["results"]
    checked = sorted(k for k, r in res.items() if r["status"] != "skipped")
    assert checked == ["2", "6c", "F3"]
    html = (_only_run(repo) / "report.html").read_text(encoding="utf-8")
    assert html.count("not checked (--only)") == 13   # 11 main rows + 2 filter rows
    assert "rows could not be read" not in html


def test_browser_start_failure_still_reports(repo, fake_smtp):
    def boom(run_dir):
        raise RuntimeError("no chromium")
    rc = runner.run([], root=repo, session_factory=boom)
    assert rc == 0
    html = fake_smtp.sent[0].get_body(("html",)).get_content()
    assert "16 rows could not be read" in html


def test_missing_credentials_exit_2(repo, monkeypatch):
    for k in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "MAIL_TO"):
        monkeypatch.delenv(k, raising=False)
    rc = runner.run(["--only", "1"], root=repo, session_factory=lambda d: (_ for _ in ()).throw(RuntimeError("x")))
    assert rc == 2


def test_test_email(repo, fake_smtp):
    assert runner.run(["--test-email"], root=repo) == 0
    assert fake_smtp.sent[0]["Subject"] == "White Build price check – test email"


def test_prune_runs(tmp_path):
    for i in range(65):
        (tmp_path / f"2026-09-{(i // 24) + 1:02d}_{(i % 24):02d}00").mkdir()
    runner.prune_runs(tmp_path, keep=60)
    left = sorted(p.name for p in tmp_path.iterdir())
    assert len(left) == 60 and left[0] == "2026-09-01_0500"


def test_attachment_names():
    assert runner.attachment_name("1", {"item": "CPU", "site": "geizhals"}, "1.png") == "01_CPU.png"
    assert runner.attachment_name("6c", {"item": "RAM alt", "group": "RAM_TFORCE", "site": "idealo"},
                                  "6c.png") == "06c_TForce_idealo.png"
    assert runner.attachment_name("F3", None, "F3_offer.png") == "F3_offer.png"


def test_attachments_downscaled_over_limit(tmp_path):
    from PIL import Image
    import os
    p = tmp_path / "big.png"
    Image.frombytes("RGB", (800, 800), os.urandom(800 * 800 * 3)).save(p)
    size = p.stat().st_size
    out = emailer.prepare_attachments([("01_CPU.png", str(p))], limit=size // 3)
    assert out[0][0] == "01_CPU.jpg" and len(out[0][1]) <= size // 3
