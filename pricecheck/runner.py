"""Orchestrates one run: read all rows one page at a time, build the report, save it, email it."""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import emailer, report
from .results import RowResult
from .sites import amazon, geizhals, idealo

ROOT = Path(__file__).resolve().parent.parent
KEEP_RUNS = 60
EXIT_OK, EXIT_CRASH, EXIT_EMAIL = 0, 1, 2

# Short names used in attachment file names, e.g. 06c_TForce_idealo.png
GROUP_SHORT = {"RAM_PATRIOT": "Patriot", "RAM_TFORCE": "TForce", "COOLER": "Cooler", "CASE": "Case"}

log = logging.getLogger("pricecheck")


# ----------------------------------------------------------------------------- setup
def setup_logging(log_dir: Path) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    # run_pricecheck.bat already appends stdout to logs\run.log; avoid writing every line twice.
    if os.environ.get("PRICECHECK_STDOUT_LOG") != "1":
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / "run.log", encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def make_run_dir(runs_dir: Path, now: datetime) -> Path:
    base = runs_dir / now.strftime("%Y-%m-%d_%H%M")
    d, n = base, 2
    while d.exists():
        d = base.with_name(f"{base.name}_{n}")
        n += 1
    d.mkdir(parents=True)
    return d


def prune_runs(runs_dir: Path, keep: int = KEEP_RUNS) -> None:
    dirs = sorted(p for p in runs_dir.iterdir() if p.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}_\d{4}", p.name))
    for old in dirs[:-keep] if len(dirs) > keep else []:
        shutil.rmtree(old, ignore_errors=True)
        log.info("deleted old run %s", old.name)


def attachment_name(row_id: str, cfg: dict | None, disk_name: str) -> str:
    """Disk name '6c.png' -> '06c_TForce_idealo.png'; filter shots keep their names ('F3_offer.png')."""
    m = re.fullmatch(r"(\d+)([a-z]?)", row_id)
    if not m or not cfg:
        return disk_name
    num = f"{int(m.group(1)):02d}{m.group(2)}"
    if cfg.get("group"):
        label = f"{GROUP_SHORT.get(cfg['group'], cfg['group'].title())}_{cfg['site']}"
    else:
        label = re.sub(r"\W+", "", cfg["item"])
    return f"{num}_{label}{Path(disk_name).suffix}"


# ----------------------------------------------------------------------------- rows
def check_item(session, item: dict) -> RowResult:
    row = RowResult(id=item["id"])
    log.info("---- row %s (%s, %s)", item["id"], item["item"], item["site"])
    shot = f"{item['id']}.png"
    try:
        if item["site"] == "geizhals":
            geizhals.read_product(session, row, item["url"], shot)
        elif item["site"] == "idealo":
            idealo.read_product(session, row, item["url"], shot)
        elif item["site"] == "amazon":
            amazon.read(session, row, item["url"], shot)
        else:
            raise ValueError(f"unknown site {item['site']!r}")
    except Exception as e:  # noqa: BLE001
        log.exception("[%s] unexpected error", item["id"])
        row.status, row.error, row.price = "failed", str(e), None
        try:
            session.dump_debug(item["id"])
        except Exception:
            pass
    log.info("[%s] result: %s %s", row.id, row.status, row.price)
    return row


def check_filter(session, flt: dict) -> RowResult:
    row = RowResult(id=flt["id"])
    log.info("---- filter %s (%s)", flt["id"], flt["label"])
    try:
        if flt["site"] == "geizhals":
            check = geizhals.gpu_card_check if flt["kind"] == "gpu" else geizhals.ram_card_check
            geizhals.read_search(session, row, flt["url"], check)
        elif flt["site"] == "idealo":
            if flt["kind"] != "ram":
                raise ValueError("idealo filters only support kind 'ram'")
            idealo.read_search(session, row, flt["url"], idealo.ram_tile_check)
        else:
            raise ValueError(f"unknown site {flt['site']!r}")
    except Exception as e:  # noqa: BLE001
        log.exception("[%s] unexpected error", flt["id"])
        row.status, row.error, row.price = "failed", str(e), None
        try:
            session.dump_debug(flt["id"])
        except Exception:
            pass
    log.info("[%s] result: %s %s %s", row.id, row.status, row.price, row.product or "")
    return row


# ----------------------------------------------------------------------------- main
def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="pricecheck.py", description="White Build price checker (no AI at runtime).")
    p.add_argument("--no-email", action="store_true", help="run and save the report only")
    p.add_argument("--only", help="check only these rows, e.g. 2,6c,F3")
    p.add_argument("--headed", action="store_true", help="show the browser window")
    p.add_argument("--test-email", action="store_true", help="send a tiny test mail and exit")
    return p.parse_args(argv)


def run(argv=None, root: Path = ROOT, session_factory=None) -> int:
    args = parse_args(argv)
    try:
        from dotenv import load_dotenv

        load_dotenv(root / ".env")
    except ImportError:
        pass
    setup_logging(root / "logs")
    config = load_config(root / "config.json")

    if args.test_email:
        try:
            emailer.send_test(config)
            log.info("test email sent")
            return EXIT_OK
        except Exception as e:  # noqa: BLE001
            log.error("test email failed: %s", e)
            return EXIT_EMAIL

    only = {x.strip().lower() for x in args.only.split(",")} if args.only else None
    selected = lambda i: only is None or i.lower() in only  # noqa: E731

    now = datetime.now(ZoneInfo(config.get("timezone", "Europe/Kyiv")))
    runs_dir = root / "runs"
    run_dir = make_run_dir(runs_dir, now)
    log.info("==== run %s (%s) ====", run_dir.name, "headed" if args.headed else "headless")

    results: dict[str, RowResult] = {}
    todo = [("item", it) for it in config["items"] if selected(it["id"])] + \
           [("filter", f) for f in config.get("filters", []) if selected(f["id"])]
    session = None
    try:
        if session_factory:
            session = session_factory(run_dir)
        else:
            from .browser import Session

            session = Session.launch(root / "browser-profile", run_dir, headed=args.headed)
    except Exception as e:  # noqa: BLE001
        log.exception("could not start the browser")
        for _, cfg in todo:
            results[cfg["id"]] = RowResult(id=cfg["id"], status="failed", error=f"browser did not start: {e}")
    if session is not None:
        try:
            for kind, cfg in todo:
                results[cfg["id"]] = check_item(session, cfg) if kind == "item" else check_filter(session, cfg)
        finally:
            try:
                session.close()
            except Exception:
                pass
    for it in config["items"] + config.get("filters", []):
        results.setdefault(it["id"], RowResult(id=it["id"], status="skipped"))

    rep = report.compute(config, results, now)
    by_id = {c["id"]: c for c in config["items"]}
    attachments, gallery = [], []
    for it in config["items"] + config.get("filters", []):
        for path in results[it["id"]].screenshots:
            p = Path(path)
            attachments.append((attachment_name(it["id"], by_id.get(it["id"]), p.name), str(p)))
            gallery.append((f"{it['id']}: {p.name}", p.name))
    report.save(rep, run_dir, results, gallery)
    log.info("report saved: %s", run_dir / "report.html")
    log.info("subject: %s", rep.subject)
    try:
        prune_runs(runs_dir)
    except Exception as e:  # noqa: BLE001
        log.warning("could not prune old runs: %s", e)

    if args.no_email:
        return EXIT_OK
    try:
        sender, pw, to = emailer.credentials(config)
        msg = emailer.build_message(rep.subject, report.render_html(rep), report.render_text(rep), sender, to,
                                    attachments)
        emailer.send(msg, sender, pw)
    except Exception as e:  # noqa: BLE001
        log.error("sending the email failed: %s (report kept at %s)", e, run_dir)
        return EXIT_EMAIL
    return EXIT_OK


def main(argv=None) -> int:
    try:
        return run(argv)
    except Exception:  # noqa: BLE001
        logging.getLogger("pricecheck").exception("run crashed")
        return EXIT_CRASH
