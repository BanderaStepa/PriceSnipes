import copy
import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pricecheck.results import RowResult

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Real results of 2026-09-29 (BUILD_SPEC §12.4)
PRICES_2026_09_29 = {
    "1": "271.54", "2": "1556.40", "3": "153.99", "4": "111.07", "5": "76.38",
    "6a": "519.90", "6b": "519.90", "6c": "486.99", "6d": "486.99",
    "7a": "50.51", "7b": "68.21", "8a": "92.82", "8b": "91.13",
    "F1": "445.26", "F2": "479.00", "F3": "1393.74",
}


@pytest.fixture
def config():
    with open(ROOT / "config.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def results_0929():
    res = {}
    for k, v in PRICES_2026_09_29.items():
        r = RowResult(id=k, status="ok", price=Decimal(v), shop="Shop", in_stock=True, stock_text="Auf Lager")
        res[k] = r
    res["4"].in_stock, res["4"].stock_text = False, "Bestellt, wird in 2 Werktagen erwartet"
    res["F1"].product = "goodram IRDM BLACK SILVER UDIMM 32GB Kit"
    res["F1"].skipped = [("Crucial Pro Overclocking 32GB Kit DDR5-6000 CL36", "CL36"),
                         ("XPG Lancer Blade 32GB Kit DDR5-6000 CL48", "CL48")]
    return res


@pytest.fixture
def now_kyiv():
    return datetime(2026, 9, 29, 12, 15, tzinfo=ZoneInfo("Europe/Kyiv"))


def _chromium_kwargs():
    exe = os.environ.get("PRICECHECK_CHROMIUM")
    if not exe and Path("/opt/pw-browsers/chromium").exists():
        exe = "/opt/pw-browsers/chromium"
    return {"executable_path": exe} if exe else {}


@pytest.fixture(scope="session")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    pw = sync_api.sync_playwright().start()
    try:
        try:
            b = pw.chromium.launch()
        except Exception:
            kw = _chromium_kwargs()
            if not kw:
                pytest.skip("no chromium available")
            b = pw.chromium.launch(**kw)
    except Exception as e:  # pragma: no cover
        pw.stop()
        pytest.skip(f"chromium cannot start: {e}")
    yield b
    b.close()
    pw.stop()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(locale="de-DE", viewport={"width": 1400, "height": 900})
    p = ctx.new_page()
    yield p
    ctx.close()


def load_fixture(page, name):
    page.goto((FIXTURES / name).as_uri())
