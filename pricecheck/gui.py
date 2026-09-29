"""Desktop control panel for the price checker (Tkinter, part of the standard Python install).

It only starts `pricecheck.py` as a separate process with the chosen options and shows its output.
Start it with the Desktop shortcut (create_shortcut.ps1) or by double-clicking control_panel.pyw.
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_NAME = "White Build price check"
IS_WINDOWS = sys.platform == "win32"
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0   # CREATE_NO_WINDOW: no black console popping up

EXIT_TEXT = {
    0: "finished OK",
    1: "crashed (see the log below / logs\\run.log)",
    2: "email could not be sent (report is saved on disk)",
}


# ----------------------------------------------------------------------------- helpers (no GUI, unit-tested)
def row_ids(config: dict) -> list[str]:
    return [i["id"] for i in config.get("items", [])] + [f["id"] for f in config.get("filters", [])]


def parse_only(text: str, valid: list[str]) -> tuple[list[str], list[str]]:
    """'2, 6c,f3' -> (['2', '6c', 'F3'], []) ; unknown ids are returned as the second list."""
    by_lower = {v.lower(): v for v in valid}
    ids, bad = [], []
    for part in re.split(r"[,\s;]+", text or ""):
        if not part:
            continue
        if part.lower() in by_lower:
            if by_lower[part.lower()] not in ids:
                ids.append(by_lower[part.lower()])
        else:
            bad.append(part)
    return ids, bad


def build_args(headed: bool = False, no_email: bool = False, only: list[str] | None = None,
               test_email: bool = False) -> list[str]:
    if test_email:
        return ["--test-email"]
    args = []
    if no_email:
        args.append("--no-email")
    if headed:
        args.append("--headed")
    if only:
        args += ["--only", ",".join(only)]
    return args


def python_exe() -> str:
    """The console python next to the running interpreter (pythonw.exe -> python.exe)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        cand = exe.with_name("python.exe")
        if cand.exists():
            return str(cand)
    return str(exe)


def latest_run(runs_dir: Path) -> Path | None:
    if not runs_dir.is_dir():
        return None
    runs = sorted(p for p in runs_dir.iterdir() if p.is_dir() and (p / "report.html").exists())
    return runs[-1] if runs else None


def run_summary(run: Path | None) -> str:
    if not run:
        return "No report yet."
    try:
        data = json.loads((run / "results.json").read_text(encoding="utf-8"))
        res = data.get("results", {})
        bad = [k for k, r in res.items() if r.get("status") not in ("ok", "skipped", "no_match")]
        extra = f"  ·  ⚠ not read: {', '.join(bad)}" if bad else ""
        return f"{run.name}:  {data.get('subject', '')}{extra}"
    except Exception:
        return f"{run.name}"


def open_path(path: Path) -> None:
    if IS_WINDOWS:
        os.startfile(str(path))  # noqa: S606 - opens with the default program
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def task_status() -> str:
    """Human-readable state of the scheduled task (Windows only)."""
    if not IS_WINDOWS:
        return "Scheduled task: only available on Windows"
    # Get-ScheduledTaskInfo gives language-independent fields (schtasks output is localized).
    ps = (f"$i = Get-ScheduledTaskInfo -TaskName '{TASK_NAME}' -ErrorAction Stop; "
          "'{0:yyyy-MM-dd HH:mm}|{1:yyyy-MM-dd HH:mm}|{2}' -f $i.NextRunTime, $i.LastRunTime, $i.LastTaskResult")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             creationflags=NO_WINDOW, timeout=30)
    except Exception as e:  # noqa: BLE001
        return f"Scheduled task: could not check ({e})"
    return format_task_status(out.returncode, out.stdout)


def format_task_status(returncode: int, stdout: str) -> str:
    parts = (stdout or "").strip().split("|")
    if returncode != 0 or len(parts) != 3:
        return "Scheduled task: NOT installed"
    nxt, last, res = parts
    res_txt = {"0": "OK", "2": "email failed", "1": "crashed", "267011": "never ran yet",
               "267009": "running now"}.get(res.strip(), f"code {res.strip()}")
    if last.startswith("1999") or not last:
        last = "never"
    return f"Scheduled task: installed  ·  next run {nxt or '?'}  ·  last run {last} ({res_txt})"


# ----------------------------------------------------------------------------- the window
class ControlPanel:
    def __init__(self, root_dir: Path = ROOT):
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText

        self.tk, self.ttk = tk, ttk
        self.root_dir = root_dir
        self.proc: subprocess.Popen | None = None
        self.lines: "queue.Queue[str | tuple]" = queue.Queue()
        try:
            self.config = json.loads((root_dir / "config.json").read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            self.config = {}
            self._config_error = str(e)
        else:
            self._config_error = None
        self.valid_ids = row_ids(self.config)

        w = self.win = tk.Tk()
        w.title("White Build price check")
        w.geometry("980x680")
        w.minsize(760, 520)
        pad = {"padx": 8, "pady": 4}

        # --- run options
        opts = ttk.LabelFrame(w, text="Run the price check")
        opts.pack(fill="x", **pad)
        self.headed = tk.BooleanVar(value=True)
        self.no_email = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Show the browser while it runs", variable=self.headed).grid(
            row=0, column=0, sticky="w", **pad)
        ttk.Checkbutton(opts, text="Don't send the email (test run)", variable=self.no_email).grid(
            row=0, column=1, sticky="w", **pad)
        ttk.Label(opts, text="Only these rows (empty = all):").grid(row=1, column=0, sticky="w", **pad)
        self.only = tk.StringVar()
        ttk.Entry(opts, textvariable=self.only, width=30).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(opts, text="Rows: " + ", ".join(self.valid_ids), foreground="#666").grid(
            row=2, column=0, columnspan=4, sticky="w", padx=8)

        btns = ttk.Frame(opts)
        btns.grid(row=3, column=0, columnspan=4, sticky="w", **pad)
        self.run_btn = ttk.Button(btns, text="▶  Run check", command=self.run_check)
        self.run_btn.pack(side="left", padx=4)
        self.full_btn = ttk.Button(btns, text="Full run + email", command=self.full_run)
        self.full_btn.pack(side="left", padx=4)
        self.test_btn = ttk.Button(btns, text="Send test email", command=self.test_email)
        self.test_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(btns, text="■  Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        # --- results / files
        files = ttk.LabelFrame(w, text="Reports and settings")
        files.pack(fill="x", **pad)
        fb = ttk.Frame(files)
        fb.pack(fill="x", **pad)
        ttk.Button(fb, text="Open latest report", command=self.open_report).pack(side="left", padx=4)
        ttk.Button(fb, text="Open runs folder", command=lambda: self._open(self.root_dir / "runs")).pack(side="left", padx=4)
        ttk.Button(fb, text="Open log file", command=lambda: self._open(self.root_dir / "logs" / "run.log")).pack(side="left", padx=4)
        ttk.Button(fb, text="Edit config.json", command=lambda: self._edit(self.root_dir / "config.json")).pack(side="left", padx=4)
        ttk.Button(fb, text="Edit .env (Gmail)", command=self.edit_env).pack(side="left", padx=4)
        self.last_var = tk.StringVar()
        ttk.Label(files, textvariable=self.last_var, wraplength=940).pack(fill="x", padx=8, pady=(0, 6))

        # --- schedule
        sch = ttk.LabelFrame(w, text="Automatic runs every 4 hours (Windows Task Scheduler)")
        sch.pack(fill="x", **pad)
        sb = ttk.Frame(sch)
        sb.pack(fill="x", **pad)
        ttk.Button(sb, text="Install / update schedule", command=self.install_task).pack(side="left", padx=4)
        ttk.Button(sb, text="Run scheduled task now", command=self.run_task_now).pack(side="left", padx=4)
        ttk.Button(sb, text="Remove schedule", command=self.remove_task).pack(side="left", padx=4)
        ttk.Button(sb, text="Refresh", command=self.refresh).pack(side="left", padx=4)
        self.task_var = tk.StringVar()
        ttk.Label(sch, textvariable=self.task_var, wraplength=940).pack(fill="x", padx=8, pady=(0, 6))

        # --- output
        out = ttk.LabelFrame(w, text="Output")
        out.pack(fill="both", expand=True, **pad)
        self.text = ScrolledText(out, height=12, font=("Consolas", 9), wrap="none")
        self.text.pack(fill="both", expand=True, padx=4, pady=4)
        self.text.tag_configure("warn", foreground="#b36b00")
        self.text.tag_configure("err", foreground="#b00020")
        self.text.tag_configure("ok", foreground="#1b7f2a")
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(w, textvariable=self.status, anchor="w").pack(fill="x", padx=10, pady=(0, 6))

        w.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh()
        self._check_setup()
        w.after(100, self._drain)

    # ------------------------------------------------------------------ helpers
    def _ui(self, fn):
        """Run `fn` on the Tk thread (Tk must not be touched from worker threads)."""
        self.lines.put(("call", fn))

    def log(self, line: str, tag: str | None = None):
        if tag is None:
            tag = "err" if re.search(r"\bERROR\b|Traceback|failed", line) else \
                  "warn" if "WARNING" in line else None
        self.text.insert("end", line if line.endswith("\n") else line + "\n", tag or ())
        self.text.see("end")

    def _open(self, path: Path):
        if not path.exists():
            self.log(f"Not found yet: {path}", "warn")
            return
        open_path(path)

    def _edit(self, path: Path):
        if IS_WINDOWS:
            subprocess.Popen(["notepad.exe", str(path)])
        else:
            open_path(path)

    def _check_setup(self):
        if self._config_error:
            self.log(f"config.json could not be read: {self._config_error}", "err")
        if not (self.root_dir / ".env").exists():
            self.log("No .env file yet: click 'Edit .env (Gmail)' to create it (needed for sending email).", "warn")
        if not (self.root_dir / ".venv").exists() and IS_WINDOWS:
            self.log("No .venv folder found: do the setup steps in README.md first.", "warn")

    def refresh(self):
        self.last_var.set("Latest report: " + run_summary(latest_run(self.root_dir / "runs")))
        self.task_var.set("Checking scheduled task…")

        def work():
            s = task_status()
            self._ui(lambda: self.task_var.set(s))
        threading.Thread(target=work, daemon=True).start()

    def _set_running(self, running: bool):
        st = "disabled" if running else "normal"
        for b in (self.run_btn, self.full_btn, self.test_btn):
            b.configure(state=st)
        self.stop_btn.configure(state="normal" if running else "disabled")

    # ------------------------------------------------------------------ actions
    def run_check(self):
        only, bad = parse_only(self.only.get(), self.valid_ids)
        if bad:
            self.log(f"Unknown row id(s): {', '.join(bad)}. Valid: {', '.join(self.valid_ids)}", "err")
            return
        self._start(build_args(self.headed.get(), self.no_email.get(), only))

    def full_run(self):
        self._start(build_args(headed=self.headed.get()))

    def test_email(self):
        self._start(build_args(test_email=True))

    def _start(self, args: list[str]):
        if self.proc:
            return
        cmd = [python_exe(), str(self.root_dir / "pricecheck.py"), *args]
        self.text.delete("1.0", "end")
        self.log("$ python pricecheck.py " + " ".join(args), "ok")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        env.pop("PRICECHECK_STDOUT_LOG", None)   # let the script write logs\run.log itself
        try:
            self.proc = subprocess.Popen(cmd, cwd=self.root_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, env=env, creationflags=NO_WINDOW,
                                         text=True, encoding="utf-8", errors="replace", bufsize=1)
        except Exception as e:  # noqa: BLE001
            self.log(f"Could not start: {e}", "err")
            self.proc = None
            return
        self._set_running(True)
        self.status.set("Running… (a full run takes a few minutes)")
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def _reader(self, proc: subprocess.Popen):
        for line in proc.stdout:
            self.lines.put(line)
        self.lines.put(("done", proc.wait()))

    def _drain(self):
        try:
            while True:
                item = self.lines.get_nowait()
                if isinstance(item, tuple) and item[0] == "done":
                    self._finished(item[1])
                elif isinstance(item, tuple) and item[0] == "call":
                    item[1]()
                else:
                    self.log(item)
        except queue.Empty:
            pass
        self.win.after(100, self._drain)

    def _finished(self, code: int):
        self.proc = None
        self._set_running(False)
        msg = EXIT_TEXT.get(code, f"stopped (exit code {code})")
        self.log(f"--- {msg} ---", "ok" if code == 0 else "err")
        self.status.set(f"Last run: {msg}")
        self.refresh()

    def stop(self):
        p = self.proc
        if not p:
            return
        self.log("Stopping…", "warn")
        if IS_WINDOWS:   # also end the browser started by the script
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, creationflags=NO_WINDOW)
        else:
            p.terminate()

    def open_report(self):
        run = latest_run(self.root_dir / "runs")
        if not run:
            self.log("No report yet: run a check first.", "warn")
            return
        open_path(run / "report.html")

    def edit_env(self):
        env, example = self.root_dir / ".env", self.root_dir / ".env.example"
        if not env.exists() and example.exists():
            shutil.copy(example, env)
            self.log("Created .env from .env.example: fill in GMAIL_ADDRESS and GMAIL_APP_PASSWORD, then save.", "ok")
        self._edit(env)

    def _powershell(self, script: str, label: str):
        if not IS_WINDOWS:
            self.log("Only available on Windows.", "warn")
            return
        self.log(f"$ {label}", "ok")

        def work():
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                               cwd=self.root_dir, capture_output=True, text=True, creationflags=NO_WINDOW)
            out = (r.stdout + r.stderr).strip()
            self._ui(lambda: (self.log(out or "done", "err" if r.returncode else "ok"), self.refresh()))
        threading.Thread(target=work, daemon=True).start()

    def install_task(self):
        self._powershell(f"& '{self.root_dir / 'setup_task.ps1'}'", "setup_task.ps1")

    def remove_task(self):
        from tkinter import messagebox
        if messagebox.askyesno("Remove schedule", f"Delete the scheduled task '{TASK_NAME}'?"):
            self._powershell(f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false; "
                             f"'Scheduled task removed.'", "remove scheduled task")

    def run_task_now(self):
        self._powershell(f"Start-ScheduledTask -TaskName '{TASK_NAME}'; "
                         f"'Started. It runs in the background; output goes to logs\\run.log.'",
                         "start scheduled task")

    def on_close(self):
        if self.proc:
            from tkinter import messagebox
            if not messagebox.askyesno("Still running", "A check is still running. Stop it and close?"):
                return
            self.stop()
        self.win.destroy()

    def mainloop(self):
        self.win.mainloop()


def main():
    ControlPanel().mainloop()


if __name__ == "__main__":
    main()
