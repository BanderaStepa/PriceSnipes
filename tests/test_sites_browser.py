"""Extractor tests against synthetic HTML fixtures in a real (headless) Chromium.

Skipped automatically if Playwright/Chromium is not available.
"""
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import FIXTURES, load_fixture
from pricecheck.browser import Session
from pricecheck.consent import dismiss_consent
from pricecheck.results import RowResult
from pricecheck.sites import amazon, geizhals, idealo


def routed_session(page, tmp_path: Path, routes: dict, overrides: dict | None = None) -> Session:
    """Serve fixtures for real shop URLs. `routes` maps URL substrings to fixture names (first match wins)."""
    overrides = overrides or {}

    def handler(route):
        url = route.request.url
        for pat, body in overrides.items():
            if pat in url:
                return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)
        for pat, fx in routes.items():
            if pat in url:
                return route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                     body=(FIXTURES / fx).read_text(encoding="utf-8"))
        return route.fulfill(status=404, body="not found")

    page.context.route("**/*", handler)
    return Session(page, tmp_path, polite_delay=None, retry_delay_s=0, consent_timeout_s=0.2, ready_timeout_ms=800)


# ----------------------------------------------------------------------------- consent
def test_consent_rejects_in_iframe(page):
    load_fixture(page, "consent_iframe.html")
    page.wait_for_timeout(300)
    assert dismiss_consent(page, 2.0)
    page.wait_for_timeout(200)
    assert page.evaluate("window.choice") == "reject"


def test_consent_rejects_in_shadow_dom(page):
    load_fixture(page, "consent_shadow.html")
    assert dismiss_consent(page, 2.0)
    assert page.evaluate("window.choice") == "necessary"


def test_consent_close_when_no_reject_option(page):
    load_fixture(page, "consent_close_only.html")
    assert dismiss_consent(page, 0.5)
    assert page.evaluate("window.choice") == "close"


def test_consent_nothing_to_do(page):
    load_fixture(page, "geizhals_product.html")
    assert dismiss_consent(page, 0.3) is False


# ----------------------------------------------------------------------------- geizhals
def test_geizhals_product_rows(page):
    load_fixture(page, "geizhals_product.html")
    offers, idx, loc = geizhals.extract_offers(page)
    assert [o.price for o in offers] == [Decimal("1556.40"), Decimal("1558.99"), Decimal("1599.00"), Decimal("1610.00")]
    assert [o.shop for o in offers] == ["MobPicker", "voelkner.de", "GadgetLifestyle (via Amazon Marketplace)",
                                        "Mindfactory"]
    assert [o.in_stock for o in offers] == [False, True, True, False]
    assert offers[0].stock_text == "4-10 Arbeitstage"
    assert offers[1].stock_text == "sofort lieferbar, Lieferzeit 1-3 Werktage"
    assert offers[3].stock_text == "Bestellt, wird in 2 Werktagen erwartet"


def test_geizhals_plain_text_rows_and_min(page):
    load_fixture(page, "geizhals_product_plain.html")
    offers, idx, loc = geizhals.extract_offers(page)
    assert len(offers) == 3   # the #offer__list container itself is ignored
    assert [o.price for o in offers] == [Decimal("115.00"), Decimal("111.07"), Decimal("119.90")]
    assert [o.shop for o in offers] == ["x-kom", "Mindfactory", "voelkner.de"]
    assert [o.in_stock for o in offers] == [True, False, True]


def test_geizhals_read_product_screenshot(page, tmp_path):
    s = routed_session(page, tmp_path, {"geizhals.de": "geizhals_product_plain.html"})
    row = geizhals.read_product(s, RowResult(id="4"), "https://geizhals.de/x-a1.html", "4.png")
    assert row.ok and row.price == Decimal("111.07") and row.shop == "Mindfactory"
    assert row.in_stock is False
    assert (tmp_path / "4.png").exists()
    assert not row.notes   # title "ab € 111,07" agrees with the lowest row


def test_geizhals_failed_and_captcha(page, tmp_path):
    s = routed_session(page, tmp_path, {}, overrides={
        "captcha": "<html><body><h1>Bitte bestätigen: sind Sie ein Mensch?</h1><div>captcha</div></body></html>",
        "empty": "<html><body><h1>Produkt</h1><p>keine Angebote</p></body></html>",
    })
    row = geizhals.read_product(s, RowResult(id="1"), "https://geizhals.de/missing-a1.html", "1.png")
    assert row.status == "failed" and row.price is None
    row = geizhals.read_product(s, RowResult(id="1"), "https://geizhals.de/captcha-a1.html", "1.png")
    assert row.status == "captcha"
    row = geizhals.read_product(s, RowResult(id="3"), "https://geizhals.de/empty-a1.html", "3.png")
    assert row.status == "no_price"
    assert (tmp_path / "debug" / "3.html").exists() and (tmp_path / "debug" / "3_full.png").exists()


def test_geizhals_search_ram(page, tmp_path):
    s = routed_session(page, tmp_path, {
        "goodram-irdm": "geizhals_product.html",   # any product page will do for the offer read
        "geizhals.de/?": "geizhals_search_ram.html",
    })
    row = geizhals.read_search(s, RowResult(id="F1"), "https://geizhals.de/?fs=32GB+Kit&sort=p",
                               geizhals.ram_card_check)
    assert row.ok
    assert row.product.startswith("goodram IRDM BLACK SILVER UDIMM 32GB Kit")
    assert row.product_url.endswith("-a3000001.html")
    reasons = [r for _, r in row.skipped]
    assert reasons == ["variant group", "CL48", "single module", "CL36"]
    assert (tmp_path / "F1_card.png").exists() and (tmp_path / "F1_offer.png").exists()


def test_geizhals_search_gpu(page, tmp_path):
    s = routed_session(page, tmp_path, {
        "gainward": "geizhals_product.html",
        "geizhals.de/?": "geizhals_search_gpu.html",
    })
    row = geizhals.read_search(s, RowResult(id="F3"), "https://geizhals.de/?fs=RTX+5080&sort=p",
                               geizhals.gpu_card_check)
    assert row.ok
    assert row.product.startswith("Gainward GeForce RTX 5080 Phoenix")
    assert [r for _, r in row.skipped] == ["variant group", "RTX 5070 Ti"]


def test_geizhals_search_no_match(page, tmp_path):
    s = routed_session(page, tmp_path, {"geizhals.de/?": "geizhals_search_gpu.html"})
    row = geizhals.read_search(s, RowResult(id="F1"), "https://geizhals.de/?fs=x", geizhals.ram_card_check)
    assert row.status == "no_match" and len(row.skipped) == 4


# ----------------------------------------------------------------------------- idealo
def test_idealo_product_structural(page):
    load_fixture(page, "idealo_product.html")
    offers, idx = idealo.extract_offers(page)
    assert [o.price for o in offers] == [Decimal("519.90"), Decimal("524.00"), Decimal("549.00")]
    assert offers[0].shop == "amazon.de marketplace (Verkauf durch: GadgetLifestyle)"
    assert offers[1].shop == "GALAXUS"
    assert offers[2].shop == "ALTERNATE"
    assert [o.in_stock for o in offers] == [True, True, False]   # green dot, "Auf Lager", yellow dot
    assert offers[0].stock_text == "Lieferung: bis Mi. 30.09."


def test_idealo_coupon_note(page, tmp_path):
    s = routed_session(page, tmp_path, {"idealo.de": "idealo_product_coupon.html"})
    row = idealo.read_product(s, RowResult(id="8a"), "https://www.idealo.de/preisvergleich/OffersOfProduct/1.html",
                              "8a.png")
    assert row.ok and row.price == Decimal("92.82")
    assert row.price_note == "(price only with voelkner+)"
    assert row.shop == "voelkner" and row.in_stock is True
    assert (tmp_path / "8a.png").exists()


def test_idealo_search(page, tmp_path):
    s = routed_session(page, tmp_path, {
        "OffersOfProduct/202204151": "idealo_product.html",
        "MainSearchProductCategory": "idealo_search.html",
    })
    row = idealo.read_search(s, RowResult(id="F2"),
                             "https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q=x",
                             idealo.ram_tile_check)
    assert row.ok
    assert row.product == "Team Group T-Force Delta RGB 32GB Kit DDR5-6000 CL30"
    assert [r for _, r in row.skipped] == ["used", "specs not listed", "single module"]
    assert (tmp_path / "F2_card.png").exists() and (tmp_path / "F2_offer.png").exists()


# ----------------------------------------------------------------------------- amazon
AMAZON_URL = "https://www.amazon.de/dp/B0GPCDJ7W5/?th=1"


def test_amazon_white_renewed(page, tmp_path):
    s = routed_session(page, tmp_path, {"amazon.de": "amazon_product.html"})
    row = amazon.read(s, RowResult(id="7a"), AMAZON_URL, "7a.png")
    assert row.ok and row.price == Decimal("50.51")        # not the sponsored 73,99
    assert row.shop == "Amazon (seller ARCTIC GmbH)"
    assert row.in_stock is True and row.stock_text == "Nur noch 1 auf Lager"
    assert (tmp_path / "7a.png").exists()


def test_amazon_wrong_variant(page, tmp_path):
    body = (FIXTURES / "amazon_product.html").read_text(encoding="utf-8").replace(
        '<span class="selection">weiß</span>', '<span class="selection">schwarz</span>')
    s = routed_session(page, tmp_path, {}, overrides={"amazon.de": body})
    row = amazon.read(s, RowResult(id="7a"), AMAZON_URL, "7a.png")
    assert row.status == "wrong_variant" and row.price is None


def test_amazon_captcha(page, tmp_path):
    s = routed_session(page, tmp_path, {"amazon.de": "amazon_captcha.html"})
    row = amazon.read(s, RowResult(id="7a"), AMAZON_URL, "7a.png")
    assert row.status == "captcha"
    assert (tmp_path / "debug" / "7a.html").exists()


def test_amazon_unavailable(page, tmp_path):
    body = (FIXTURES / "amazon_product.html").read_text(encoding="utf-8").replace(
        "Nur noch 1 auf Lager", "Derzeit nicht verfügbar.")
    s = routed_session(page, tmp_path, {}, overrides={"amazon.de": body})
    row = amazon.read(s, RowResult(id="7a"), AMAZON_URL, "7a.png")
    assert row.ok and row.in_stock is False
