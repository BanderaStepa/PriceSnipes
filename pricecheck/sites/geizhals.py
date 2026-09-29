"""geizhals.de: product offer lists (items 1–5, 6b, 6d, 7b, 8b) and search results (F1, F3).

All selectors and phrases are at the top so they are easy to fix after a site change.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urljoin

from ..parse import classify_stock, find_prices, parse_price
from ..results import Offer, RowResult, apply_offer
from .common import PAYMENT_WORDS_RE, cheapest, clean, extract_availability, log_offers, marketplace_note

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------- constants
OFFER_ROW_SELECTORS = ["#offer__list .offer", ".offerlist .offer", '[id^="offer__"]']
OFFER_HEADER_SELECTORS = ["#offer__list .offerlist__header", ".offerlist__header", "#offer__list .offer__header"]
OFFER_PRICE_SELECTORS = [".offer__price .gh_price", ".offer__price", ".gh_price", "[class*=price]"]
OFFER_SHOP_SELECTORS = [".offer__merchant-name", ".merchant__logo-caption", ".offer__merchant .merchant__name",
                        "[class*=merchant-name]", "[class*=merchant__name]"]
OFFER_AVAIL_SELECTORS = [".offer__delivery-time", ".offer__availability", ".offer__delivery",
                         "[class*=delivery-time]", "[class*=availability]", "[class*=delivery]"]

CARD_SELECTORS = [".listview__item", ".productlist__product", ".galleryview__item", "article"]
CARD_NAME_SELECTORS = [".listview__name-link", ".listview__name", ".productlist__name", ".galleryview__name",
                       "h3", "h2"]
PRODUCT_HREF_RE = r"-a\d+\.html"     # real product pages look like https://geizhals.de/<slug>-a<digits>.html
VARIANT_MARKER = "VARIANTEN"

SHOP_AFTER = "zum Angebot"
SHOP_LABELS_TO_STRIP = re.compile(r"^\s*(GUTSCHEINAKTION|AKTION|DEAL|TOP-ANGEBOT)\s*", re.I)
SHOP_NAME_STOP_RE = re.compile(r"\s+(?:Hinweis:|Infos\b|AGB\b|Bewertung|Händlerbewertung|\d+\s+Bewertungen|\()", re.I)
CARD_PRICE_RE = re.compile(r"(?:ab|um)\s*€\s*[\d.,]+|(?:ab|um)\s*[\d.,]+\s*€", re.I)
TITLE_PRICE_RE = re.compile(r"\bab\s*€\s*[\d.,]+", re.I)

# JS that turns a list of elements into plain data (one browser round trip per page).
_JS_ROWS = """
(els, sel) => {
  const pick = (el, sels) => {
    for (const s of sels) {
      const x = el.querySelector(s);
      if (x && (x.innerText || '').trim()) return x.innerText.trim();
    }
    return '';
  };
  // Keep only the innermost matches (fallback selectors may also match containers).
  const set = new Set(els);
  return els.map((el, i) => ({
    index: i,
    nested_container: [...el.querySelectorAll('*')].some(c => set.has(c)),
    text: el.innerText || '',
    price_text: pick(el, sel.price),
    shop_text: pick(el, sel.shop),
    avail_text: pick(el, sel.avail),
    img_alts: [...el.querySelectorAll('img')]
                .map(im => (im.getAttribute('alt') || im.getAttribute('title') || '').trim())
                .filter(Boolean),
  }));
}
"""

_JS_CARDS = """
(els, sel) => {
  const set = new Set(els);
  const re = new RegExp(sel.href_re);
  return els.map((el, i) => {
    let p = el.parentElement, nested = false;
    while (p) { if (set.has(p)) { nested = true; break; } p = p.parentElement; }
    const links = [...el.querySelectorAll('a[href]')];
    const prod = links.find(a => re.test(a.getAttribute('href') || ''));
    let name = '';
    for (const s of sel.name) {
      const x = el.querySelector(s);
      if (x && (x.innerText || '').trim()) { name = x.innerText.trim(); break; }
    }
    if (!name && prod) name = (prod.innerText || '').trim();
    return { index: i, nested, text: el.innerText || '', name, href: prod ? prod.href : null };
  });
}
"""


# ----------------------------------------------------------------------------- pure parsing
def shop_from_row_text(text: str) -> str | None:
    """Shop name = the text right after 'zum Angebot' (labels like GUTSCHEINAKTION stripped)."""
    i = text.find(SHOP_AFTER)
    if i == -1:
        i = text.lower().find(SHOP_AFTER.lower())
    if i == -1:
        return None
    after = text[i + len(SHOP_AFTER):].lstrip(" \t\n ")
    after = SHOP_LABELS_TO_STRIP.sub("", after)
    line = after.split("\n", 1)[0]
    line = SHOP_NAME_STOP_RE.split(line, 1)[0]
    line = clean(line)
    if len(line) > 40:
        line = line.split(" ")[0]
    return line or None


def parse_offer(raw: dict) -> Offer:
    text = raw.get("text") or ""
    price = parse_price(raw.get("price_text") or "")
    if price is None:
        # The row's innerText starts with the price: "€ 1556,40 zum Angebot ..."
        price = parse_price(text)
    shop = shop_from_row_text(text)
    if not shop:
        shop = clean(raw.get("shop_text")) or None
    if not shop:
        alts = [a for a in raw.get("img_alts") or [] if not PAYMENT_WORDS_RE.search(a)]
        shop = alts[0] if alts else None
    if shop:
        shop = SHOP_LABELS_TO_STRIP.sub("", shop).strip() or None
    shop = marketplace_note(text, shop)
    avail = extract_availability(raw.get("avail_text") or "") or extract_availability(
        text.split(SHOP_AFTER, 1)[-1])
    in_stock, stock_text = classify_stock(avail)
    return Offer(price=price, shop=shop, in_stock=in_stock, stock_text=stock_text or None, raw_text=text[:500])


@dataclass
class Card:
    index: int
    name: str
    text: str
    href: str | None
    price: Decimal | None
    is_variant: bool


def parse_card(raw: dict, base_url: str = "https://geizhals.de/") -> Card:
    text = raw.get("text") or ""
    name = clean(raw.get("name")) or clean(text.strip().split("\n", 1)[0])
    m = CARD_PRICE_RE.search(text)
    price = parse_price(m.group(0)) if m else None
    if price is None:
        ps = find_prices(text)
        price = ps[0] if ps else None
    href = raw.get("href")
    is_variant = text.lstrip().upper().startswith(VARIANT_MARKER) or name.upper().startswith(VARIANT_MARKER) or not href
    return Card(index=raw.get("index", 0), name=name, text=clean(text), price=price,
                href=urljoin(base_url, href) if href else None, is_variant=is_variant)


def order_cards(cards: list[Card]) -> list[Card]:
    """Card order can differ slightly from strict price order: sort by card price (no price last)."""
    return sorted(cards, key=lambda c: (c.price is None, c.price or Decimal(0), c.index))


# ----------------------------------------------------------------------------- browser part
def _row_locator(page):
    for sel in OFFER_ROW_SELECTORS:
        loc = page.locator(sel)
        try:
            if loc.count():
                return sel, loc
        except Exception:
            continue
    return None, None


def extract_offers(page) -> tuple[list[Offer], list[int], object]:
    """Return (offers, element indexes, locator) for the offer rows on a loaded product page."""
    sel, loc = _row_locator(page)
    if loc is None:
        return [], [], None
    raws = loc.evaluate_all(_JS_ROWS, {"price": OFFER_PRICE_SELECTORS, "shop": OFFER_SHOP_SELECTORS,
                                        "avail": OFFER_AVAIL_SELECTORS})
    offers, idx = [], []
    for r in raws:
        if r.get("nested_container"):
            continue
        o = parse_offer(r)
        if o.price is None:
            continue
        offers.append(o)
        idx.append(r["index"])
    log.debug("geizhals: selector %s -> %d rows, %d with price", sel, len(raws), len(offers))
    return offers, idx, loc


def read_product(session, row: RowResult, url: str, shot_name: str, from_search: bool = False) -> RowResult:
    """Read the lowest offer of a geizhals product page into `row`."""
    status = session.open(url, ", ".join(OFFER_ROW_SELECTORS))
    if status != "ok":
        row.status = "failed"
        return row
    page = session.page
    row.page_title = session.title()
    log.info("[%s] title: %s", row.id, row.page_title)
    try:
        offers, idx, loc = extract_offers(page)
    except Exception as e:  # noqa: BLE001
        log.exception("[%s] extractor error", row.id)
        offers, idx, loc = [], [], None
        row.error = str(e)
    row.offers_seen = len(offers)
    log_offers(row.id, offers)
    best = cheapest(offers)
    if best is None:
        row.status = "captcha" if session.is_captcha() else "no_price"
        session.dump_debug(row.id if not from_search else f"{row.id}_offer")
        return row
    i, offer = best
    apply_offer(row, offer)
    m = TITLE_PRICE_RE.search(row.page_title or "")
    if m:
        tp = parse_price(m.group(0))
        if tp is not None and tp != offer.price:
            log.warning("[%s] title says 'ab %s' but lowest parsed row is %s", row.id, tp, offer.price)
            row.notes.append(f"title says ab €{tp}")
    shot = session.screenshot_element(loc.nth(idx[i]), shot_name)
    if shot:
        row.screenshots.append(shot)
    log.info("[%s] decision: %s at %s (%s)", row.id, offer.price, offer.shop, offer.stock_text)
    return row


def extract_cards(page) -> tuple[list[Card], object]:
    for sel in CARD_SELECTORS:
        loc = page.locator(sel)
        try:
            n = loc.count()
        except Exception:
            n = 0
        if not n:
            continue
        raws = loc.evaluate_all(_JS_CARDS, {"href_re": PRODUCT_HREF_RE, "name": CARD_NAME_SELECTORS})
        cards = [parse_card(r, page.url) for r in raws if not r.get("nested")]
        cards = [c for c in cards if c.text]
        if cards:
            log.debug("geizhals search: selector %s -> %d cards", sel, len(cards))
            return cards, loc
    return [], None


def read_search(session, row: RowResult, url: str, check) -> RowResult:
    """F1/F3: walk the price-sorted results, take the first product that matches `check`."""
    status = session.open(url, ", ".join(CARD_SELECTORS))
    if status != "ok":
        row.status = "failed"
        return row
    page = session.page
    row.page_title = session.title()
    cards, loc = extract_cards(page)
    log.info("[%s] %d result cards", row.id, len(cards))
    if not cards:
        row.status = "captcha" if session.is_captcha() else "no_price"
        session.dump_debug(row.id)
        return row
    chosen = None
    for c in order_cards(cards):
        if c.is_variant:
            ok, reason = False, "variant group"
        else:
            ok, reason = check(c)
        log.info("[%s]   %s | %s | %s", row.id, c.price, c.name[:80], "MATCH" if ok else f"skip: {reason}")
        if ok:
            chosen = c
            break
        row.skipped.append((c.name, reason))
    if chosen is None:
        row.status = "no_match"
        return row
    row.product = chosen.name
    row.product_url = chosen.href
    shot = session.screenshot_element(loc.nth(chosen.index), f"{row.id}_card.png")
    if shot:
        row.screenshots.append(shot)
    read_product(session, row, chosen.href, f"{row.id}_offer.png", from_search=True)
    if not row.ok and chosen.price is not None and row.status in ("no_price", "failed"):
        log.warning("[%s] product page unreadable (%s); using the search card price", row.id, row.status)
        row.notes.append(f"product page: {row.status_text()}; price taken from the search result card")
        row.price, row.status = chosen.price, "ok"
    return row


def ram_card_check(card: Card):
    from ..specs import check_ram
    return check_ram(card.name, card.text)


def gpu_card_check(card: Card):
    from ..specs import check_gpu
    return check_gpu(card.text, card.is_variant)
