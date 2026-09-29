"""The control panel's non-GUI helpers, plus a smoke test of the window if Tk and a display exist."""
import json
import os
import sys
from pathlib import Path

import pytest

from pricecheck import gui

IDS = ["1", "2", "3", "4", "5", "6a", "6b", "6c", "6d", "7a", "7b", "8a", "8b", "F1", "F2", "F3"]


def test_row_ids(config):
    assert gui.row_ids(config) == IDS


def test_parse_only():
    assert gui.parse_only("2, 6c,f3", IDS) == (["2", "6c", "F3"], [])
    assert gui.parse_only("", IDS) == ([], [])
    assert gui.parse_only("2 2;7A", IDS) == (["2", "7a"], [])
    assert gui.parse_only("9, x", IDS) == ([], ["9", "x"])


def test_build_args():
    assert gui.build_args() == []
    assert gui.build_args(headed=True, no_email=True, only=["2", "F3"]) == ["--no-email", "--headed", "--only", "2,F3"]
    assert gui.build_args(headed=True, test_email=True) == ["--test-email"]


def test_latest_run_and_summary(tmp_path):
    assert gui.latest_run(tmp_path / "runs") is None
    assert gui.run_summary(None) == "No report yet."
    for name in ("2026-09-29_0800", "2026-09-29_1200", "2026-09-29_1600"):
        d = tmp_path / "runs" / name
        d.mkdir(parents=True)
        if name != "2026-09-29_1600":          # newest has no report (still running)
            (d / "report.html").write_text("x")
    run = gui.latest_run(tmp_path / "runs")
    assert run.name == "2026-09-29_1200"
    (run / "results.json").write_text(json.dumps({
        "subject": "White Build prices – total €2,830.92",
        "results": {"1": {"status": "ok"}, "6a": {"status": "captcha"}, "F1": {"status": "no_match"}}}))
    s = gui.run_summary(run)
    assert "€2,830.92" in s and "not read: 6a" in s and "F1" not in s


def test_format_task_status():
    assert gui.format_task_status(1, "") == "Scheduled task: NOT installed"
    s = gui.format_task_status(0, "2026-09-29 16:00|2026-09-29 12:00|0\n")
    assert "next run 2026-09-29 16:00" in s and "(OK)" in s
    assert "last run never (never ran yet)" in gui.format_task_status(0, "2026-09-29 16:00|1999-11-30 00:00|267011")


def test_python_exe_prefers_console_python(tmp_path, monkeypatch):
    (tmp_path / "pythonw.exe").write_text("")
    (tmp_path / "python.exe").write_text("")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    assert gui.python_exe() == str(tmp_path / "python.exe")


def test_window_runs_a_check(tmp_path):
    """Open the real window, click 'Run check' with a bad row id, then with a real (offline) run."""
    tk = pytest.importorskip("tkinter")
    try:
        probe = tk.Tk()
        probe.destroy()
    except Exception as e:
        pytest.skip(f"no display: {e}")
    root = Path(__file__).resolve().parent.parent
    panel = gui.ControlPanel(root)
    try:
        panel.only.set("99")
        panel.run_check()
        assert "Unknown row id(s): 99" in panel.text.get("1.0", "end")
        assert panel.proc is None
        # A real subprocess: --test-email without credentials fails fast with exit code 2.
        env_backup = {k: os.environ.pop(k, None) for k in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "MAIL_TO")}
        try:
            panel.test_email()
            assert panel.proc is not None and str(panel.stop_btn["state"]) == "normal"
            for _ in range(300):
                panel.win.update()
                if panel.proc is None:
                    break
                panel.win.after(50)
        finally:
            os.environ.update({k: v for k, v in env_backup.items() if v})
        out = panel.text.get("1.0", "end")
        assert "$ python pricecheck.py --test-email" in out
        assert "email could not be sent" in out
        assert str(panel.run_btn["state"]) == "normal"
    finally:
        panel.win.destroy()
