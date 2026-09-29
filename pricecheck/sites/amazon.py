"""amazon.de (7a): renewed ARCTIC Liquid Freezer III Pro 240 A-RGB, white variant (th=1).

All selectors and phrases are at the top so they are easy to fix after a site change.
"""
from __future__ import annotations

import logging
import re

from ..parse import classify_stock, parse_price
from ..results import RowResult
from .common import clean

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------- constants
# Tried in this order. Never the sponsored banner at the top (different product at 73,99).
PRICE_CONTAINERS = [
    "#corePrice_feature_div",
    "#corePriceDisplay_desktop_feature_div .priceToPay",
    "#apex_desktop",
    "#buybox",
    "#desktop_buybox",
]
READY_SELECTOR = "#corePrice_feature_div, #corePriceDisplay_desktop_feature_div, #apex_desktop, #buybox, #availability"
AVAILABILITY_SELECTORS = ["#availability", "#availabilityInsideBuyBox_feature_div", "#outOfStock"]
SELLER_SELECTORS = ["#sellerProfileTriggerId", "#merchantInfoFeature_feature_div .offer-display-feature-text-message",
                    "#merchantInfoFeature_feature_div", "#merchant-info", "#tabular-buybox"]
VARIANT_SELECTORS = ["#variation_color_name", "#inline-twister-expanded-dimension-text-color_name",
                     "#inline-twister-row-color_name", "#twister"]
WANTED_COLOUR_RE = re.compile(r"(?:Farbe|Colou?r)\s*:?\s*(weiß|weiss|white)\b", re.I)
ANY_COLOUR_RE = re.compile(r"(?:Farbe|Colou?r)\s*:\s*([^\n]+)", re.I)
SELLER_TEXT_RE = re.compile(r"(?:Verkäufer|Verkauf durch|Sold by|Seller)\s*:?\s*([^\n]+)", re.I)
UNAVAILABLE_RE = re.compile(r"derzeit nicht verfügbar|currently unavailable", re.I)

# First .a-offscreen inside the container that is not a struck-through old price.
_JS_PRICE = """
(root) => {
  for (const el of root.querySelectorAll('.a-offscreen')) {
    if (el.closest('.a-text-price, [data-a-strike=true], .basisPrice')) continue;
    const t = (el.textContent || '').trim();
    if (t) return t;
  }
  const w = root.querySelector('.a-price:not(.a-text-price) .a-price-whole');
  const f = root.querySelector('.a-price:not(.a-text-price) .a-price-fraction');
  if (w) return (w.textContent || '').replace(/[.,\\s]+$/, '') + ',' + ((f && f.textContent) || '00').trim() + ' €';
  return '';
}
"""


def _first_text(page, selectors) -> str:
    for s in selectors:
        loc = page.locator(s)
        try:
            if loc.count():
                t = clean(loc.first.inner_text(timeout=2000))
                if t:
                    return t
        except Exception:
            continue
    return ""


def seller_from_text(text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    m = SELLER_TEXT_RE.search(text)
    s = m.group(1) if m else text
    s = re.split(r"\s{2,}|\||\bund\b|Versand durch|Versender", s)[0]
    return clean(s) or None


def read(session, row: RowResult, url: str, shot_name: str) -> RowResult:
    status = session.open(url, READY_SELECTOR)
    if status != "ok":
        row.status = "failed"
        return row
    page = session.page
    row.page_title = session.title()
    log.info("[%s] title: %s", row.id, row.page_title)
    if session.is_captcha():
        row.status = "captcha"
        session.dump_debug(row.id)
        return row

    variant_text = _first_text(page, VARIANT_SELECTORS)
    body = session.body_text()
    if not (WANTED_COLOUR_RE.search(variant_text) or WANTED_COLOUR_RE.search(body)):
        m = ANY_COLOUR_RE.search(variant_text) or ANY_COLOUR_RE.search(body)
        log.warning("[%s] wrong variant: %s", row.id, clean(m.group(0)) if m else "no 'Farbe: weiß' on page")
        row.status = "wrong_variant"
        session.dump_debug(row.id)
        return row

    price = None
    used = None
    for sel in PRICE_CONTAINERS:
        loc = page.locator(sel)
        try:
            if not loc.count():
                continue
            t = loc.first.evaluate(_JS_PRICE)
        except Exception:
            continue
        price = parse_price(t or "")
        if price is not None:
            used = sel
            break
    avail = _first_text(page, AVAILABILITY_SELECTORS)
    seller = None
    for s in SELLER_SELECTORS:
        seller = seller_from_text(_first_text(page, [s]))
        if seller:
            break
    in_stock, stock_text = classify_stock(avail)
    if in_stock is None and UNAVAILABLE_RE.search(body):
        in_stock, stock_text = False, "Derzeit nicht verfügbar"
    log.info("[%s] price %s (from %s) | stock %r | seller %r", row.id, price, used, avail, seller)
    if price is None:
        row.status = "no_price"
        session.dump_debug(row.id)
        return row
    row.price = price
    row.status = "ok"
    row.shop = f"Amazon (seller {seller})" if seller else "Amazon"
    row.in_stock, row.stock_text = in_stock, stock_text or None
    try:
        page.locator(used).first.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    shot = session.screenshot_viewport(shot_name)
    if shot:
        row.screenshots.append(shot)
    log.info("[%s] decision: %s at %s (%s)", row.id, row.price, row.shop, row.stock_text)
    return row
