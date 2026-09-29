"""idealo.de: product offer lists (6a, 6c, 8a) and search results (F2).

idealo changes class names often, so offer rows are found structurally: every "Zum Shop"
button inside the "Preisvergleich" box, climbed up to the row that also holds a € price.
All selectors and phrases are at the top so they are easy to fix after a site change.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urljoin

from ..parse import classify_stock, find_price_matches, find_prices, parse_price
from ..results import Offer, RowResult, apply_offer
from .common import PAYMENT_WORDS_RE, cheapest, clean, extract_availability, log_offers

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------- constants
# Tried first; if none match, rows are located structurally from the "Zum Shop" buttons.
OFFER_ROW_SELECTORS = ["li.productOffers-listItem", "[data-testid='offer-row']"]
SHOP_BUTTON_RE = r"zum\s*shop"
SECTION_HEADING_RE = r"^\s*Preisvergleich\b"
DELIVERY_RE = r"auf lager|lieferung|lieferbar|werktag|arbeitstag|\bbis\s+\w{2}\.?\s*\d{1,2}\.\d{1,2}|verfügbar|nicht lagernd"
READY_SELECTOR = "a:has-text('Zum Shop'), button:has-text('Zum Shop'), li.productOffers-listItem"

TAB_NEW_RE = re.compile(r"^\s*Neu\b", re.I)
COUPON_RE = re.compile(r"Preis\s+nur\s+mit\s+([^\n|]+?)(?:\s*[|\n]|\s{2,}|$)|(voelkner\+)|(Preis\s+inkl\.?\s+Gutschein)", re.I)
SELLER_RE = re.compile(r"Verkauf\s+durch:?\s*([^\n|]+)", re.I)
USED_RE = re.compile(r"\bgebraucht\b|b-ware", re.I)

TILE_SELECTORS = ["[data-testid='resultItem']", ".sr-resultList__item", ".sr-resultItem", ".offerList-item",
                  "[class*=resultList__item]", "[class*=resultItem]"]
TILE_NAME_SELECTORS = ["[class*=title]", "[class*=Title]", "[class*=name]", "h2", "h3"]
PRODUCT_HREF_PART = "OffersOfProduct"
TILE_PRICE_RE = re.compile(r"\bab\s*[\d.,]+\s*€", re.I)

_JS_OFFER_ROWS = r"""
(cfg) => {
  const PRICE = /\d{1,3}(?:\.\d{3})*,\d{2}\s*€|€\s*\d/;
  const BTN = new RegExp(cfg.button_re, 'i');
  const DELIV = new RegExp(cfg.delivery_re, 'i');
  const btns = (root) => [...root.querySelectorAll('a,button')].filter(e => {
    const t = (e.innerText || e.textContent || '').trim();
    return t.length < 30 && BTN.test(t);
  });
  let rows = [], method = '';
  for (const s of cfg.row_selectors) {
    let f = [];
    try { f = [...document.querySelectorAll(s)].filter(e => PRICE.test(e.innerText || '')); } catch (e) {}
    if (f.length) { rows = f; method = s; break; }
  }
  if (!rows.length) {
    let scope = document.body;
    const H = new RegExp(cfg.heading_re, 'i');
    const heads = [...document.querySelectorAll('h1,h2,h3,h4,h5,div,span,section,p')]
      .filter(e => H.test(e.textContent || '') && (e.textContent || '').trim().length < 60
                   && (e.childElementCount === 0 || /^H\d$/.test(e.tagName)));
    outer: for (const h of heads) {
      let a = h.parentElement;
      while (a && a !== document.body) {
        if (btns(a).length) { scope = a; break outer; }
        a = a.parentElement;
      }
    }
    const set = new Set();
    for (const b of btns(scope)) {
      let el = b.parentElement, row = null;
      while (el && el !== document.body) {
        if (PRICE.test(el.innerText || '')) { row = el; break; }
        if (el === scope) break;
        el = el.parentElement;
      }
      if (!row) continue;
      while (row.parentElement && row.parentElement !== scope && row.parentElement !== document.body
             && btns(row.parentElement).length === 1
             && (row.parentElement.innerText || '').length < 2000) row = row.parentElement;
      set.add(row);
    }
    rows = [...set];
    method = 'structural' + (scope === document.body ? ' (no Preisvergleich heading)' : '');
  }

  const colourOf = (s) => {
    const m = /rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)(?:[,\s/]+([\d.]+))?/.exec(s || '');
    if (!m) return null;
    const [r, g, b] = [+m[1], +m[2], +m[3]];
    const a = m[4] === undefined ? 1 : +m[4];
    if (a === 0) return null;
    if (g > 100 && g > r * 1.3 && g > b * 1.1) return 'green';
    if (r > 180 && g > 90 && b < 120 && r >= g) return (g > 170 ? 'yellow' : 'orange');
    if (r > 150 && g < 90 && b < 90) return 'red';
    return null;
  };
  const classColour = (cls) => {
    cls = (cls || '').toString();
    if (!/dot|status|bullet|indicator|circle|ampel|availability|delivery/i.test(cls)) return null;
    if (/green|gruen|grün|success|available(?!.*un)/i.test(cls)) return 'green';
    if (/yellow|gelb/i.test(cls)) return 'yellow';
    if (/orange|warning/i.test(cls)) return 'orange';
    if (/red|\brot\b|danger|unavailable/i.test(cls)) return 'red';
    return null;
  };
  const dotIn = (root) => {
    if (!root) return null;
    const all = [root, ...root.querySelectorAll('*')];
    for (const e of all) { const c = classColour(e.className && e.className.baseVal !== undefined ? e.className.baseVal : e.className); if (c) return c; }
    for (const e of all) {
      for (const pseudo of ['::before', '::after']) {
        const st = getComputedStyle(e, pseudo);
        if (st && st.content && st.content !== 'none') {
          const w = parseFloat(st.width) || 0, h = parseFloat(st.height) || 0;
          if (w > 0 && w <= 16 && h > 0 && h <= 16) { const c = colourOf(st.backgroundColor); if (c) return c; }
        }
      }
      const r = e.getBoundingClientRect();
      if (r.width > 0 && r.width <= 16 && r.height > 0 && r.height <= 16 && !(e.innerText || '').trim()) {
        const st = getComputedStyle(e);
        const c = colourOf(st.backgroundColor) || (e.tagName.toLowerCase() === 'svg' || e.closest('svg') ? colourOf(st.fill) : null);
        if (c) return c;
      }
    }
    return null;
  };

  const out = rows.map((row, i) => {
    // biggest price text in the row = the offer price (not the "inkl. Versand" one)
    let big = '', bigSize = 0;
    for (const e of row.querySelectorAll('*')) {
      const own = [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim();
      const t = own || (e.childElementCount === 0 ? (e.innerText || '').trim() : '');
      if (!t || !PRICE.test(t) || /inkl|versand|gesamt/i.test(t)) continue;
      const fs = parseFloat(getComputedStyle(e).fontSize) || 0;
      if (fs > bigSize) { bigSize = fs; big = t; }
    }
    // delivery cell = innermost element whose text looks like a delivery status
    let deliv = null;
    for (const e of row.querySelectorAll('*')) {
      const t = (e.innerText || '').trim();
      if (!t || t.length > 120 || !DELIV.test(t)) continue;
      if ([...e.children].some(c => DELIV.test((c.innerText || '').trim()))) continue;
      deliv = e; break;
    }
    let delivText = '';
    if (deliv) {
      const p = deliv.parentElement;
      delivText = (p && (p.innerText || '').trim().length < 100 ? p.innerText : deliv.innerText).trim();
    }
    const dot = deliv ? (dotIn(deliv) || dotIn(deliv.parentElement) || dotIn(deliv.parentElement && deliv.parentElement.parentElement)) : null;
    const imgs = [...row.querySelectorAll('img')].map(im => ({
      alt: (im.getAttribute('alt') || '').trim(), title: (im.getAttribute('title') || '').trim(),
      cls: ((im.className || '') + ' ' + ((im.parentElement && im.parentElement.className) || '')).toString(),
      src: im.getAttribute('src') || '' }));
    return { index: i, text: row.innerText || '', big_price: big, delivery: delivText, dot, imgs };
  });
  // Tag rows so the cheapest one can be screenshotted afterwards.
  document.querySelectorAll('[data-pricecheck-row]').forEach(e => e.removeAttribute('data-pricecheck-row'));
  rows.forEach((r, i) => r.setAttribute('data-pricecheck-row', String(i)));
  return { method, rows: out };
}
"""

_JS_TILES = r"""
(cfg) => {
  let els = [], method = '';
  for (const s of cfg.tile_selectors) {
    let f = [];
    try { f = [...document.querySelectorAll(s)]; } catch (e) {}
    const set = new Set(f);
    f = f.filter(e => { let p = e.parentElement; while (p) { if (set.has(p)) return false; p = p.parentElement; } return true; });
    f = f.filter(e => /€/.test(e.innerText || ''));
    if (f.length >= 2) { els = f; method = s; break; }
  }
  if (!els.length) {
    const set = new Set();
    for (const a of document.querySelectorAll('a[href*="' + cfg.href_part + '"]')) {
      let el = a.parentElement, row = null;
      while (el && el !== document.body) { if (/€/.test(el.innerText || '')) { row = el; break; } el = el.parentElement; }
      if (!row) continue;
      const hrefOf = (x) => new Set([...x.querySelectorAll('a[href*="' + cfg.href_part + '"]')].map(y => y.href.split('?')[0]));
      while (row.parentElement && row.parentElement !== document.body && hrefOf(row.parentElement).size === 1) row = row.parentElement;
      set.add(row);
    }
    els = [...set]; method = 'structural';
  }
  window.__pricecheckTiles = els;
  return { method, tiles: els.map((el, i) => {
    let name = '';
    for (const s of cfg.name_selectors) {
      const x = el.querySelector(s);
      if (x && (x.innerText || '').trim()) { name = x.innerText.trim(); break; }
    }
    const prod = el.querySelector('a[href*="' + cfg.href_part + '"]') || el.querySelector('a[href]');
    if (!name && prod) name = (prod.innerText || '').trim();
    return { index: i, text: el.innerText || '', name, href: prod ? prod.href : null };
  }) };
}
"""


# ----------------------------------------------------------------------------- pure parsing
def _price_from_text(text: str) -> Decimal | None:
    """Fallback: first price in the row that is not the 'inkl. Versand' total."""
    for price, start, end in find_price_matches(text):
        after = text[end:end + 25].lower()
        before = text[max(0, start - 25):start].lower()
        if "inkl" in after or "versand" in after or "gesamt" in before:
            continue
        return price
    ps = find_prices(text)
    return ps[0] if ps else None


def shop_from_imgs(imgs: list[dict]) -> str | None:
    cands = []
    for im in imgs or []:
        label = im.get("alt") or im.get("title") or ""
        if not label or PAYMENT_WORDS_RE.search(label):
            continue
        score = 1 if re.search(r"shop|logo|merchant", (im.get("cls") or "") + " " + (im.get("src") or ""), re.I) else 0
        cands.append((-score, label))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    return clean(cands[0][1])


def coupon_note(text: str) -> str | None:
    m = COUPON_RE.search(text or "")
    if not m:
        return None
    what = clean(m.group(1) or m.group(2) or "coupon")
    if m.group(3):
        what = "coupon"
    return f"(price only with {what})"


def parse_offer(raw: dict) -> Offer:
    text = raw.get("text") or ""
    price = parse_price(raw.get("big_price") or "")
    if price is None:
        price = _price_from_text(text)
    shop = shop_from_imgs(raw.get("imgs") or [])
    seller = SELLER_RE.search(text)
    if seller:
        s = clean(seller.group(1))
        shop = f"{shop} (Verkauf durch: {s})" if shop else s
    delivery = clean(raw.get("delivery")) or extract_availability(text)
    in_stock, stock_text = classify_stock(delivery, raw.get("dot"))
    return Offer(price=price, shop=shop or "unknown shop", in_stock=in_stock, stock_text=stock_text or None,
                 price_note=coupon_note(text), raw_text=text[:500])


@dataclass
class Tile:
    index: int
    name: str
    text: str
    href: str | None
    price: Decimal | None
    used: bool
    is_product_page: bool
    raw_text: str = ""


def parse_tile(raw: dict, base_url: str = "https://www.idealo.de/") -> Tile:
    text = raw.get("text") or ""
    name = clean(raw.get("name")) or clean(text.strip().split("\n", 1)[0])
    m = TILE_PRICE_RE.search(text)
    price = parse_price(m.group(0)) if m else None
    if price is None:
        ps = find_prices(text)
        price = ps[0] if ps else None
    href = raw.get("href")
    href = urljoin(base_url, href) if href else None
    return Tile(index=raw.get("index", 0), name=name, text=clean(text), href=href, price=price,
                used=bool(USED_RE.search(text)), is_product_page=bool(href and PRODUCT_HREF_PART in href),
                raw_text=text)


# ----------------------------------------------------------------------------- browser part
def _ensure_new_tab(page):
    """Make sure the 'Neu' offers tab is selected (not 'B-Ware & Gebraucht'). It is the default."""
    try:
        tab = page.get_by_role("tab", name=TAB_NEW_RE)
        if tab.count() and tab.first.get_attribute("aria-selected") == "false":
            log.info("idealo: selecting the 'Neu' tab")
            tab.first.click(timeout=3000)
            page.wait_for_timeout(2000)
    except Exception as e:  # noqa: BLE001
        log.debug("idealo: tab check failed: %s", e)


def extract_offers(page) -> tuple[list[Offer], list[int]]:
    res = page.evaluate(_JS_OFFER_ROWS, {"row_selectors": OFFER_ROW_SELECTORS, "button_re": SHOP_BUTTON_RE,
                                         "heading_re": SECTION_HEADING_RE, "delivery_re": DELIVERY_RE})
    log.info("idealo: row detection method: %s, %d rows", res["method"], len(res["rows"]))
    if not res["rows"]:
        log.warning("idealo: 0 offer rows found")
    offers, idx = [], []
    for r in res["rows"]:
        o = parse_offer(r)
        if o.price is None:
            continue
        offers.append(o)
        idx.append(r["index"])
    return offers, idx


def read_product(session, row: RowResult, url: str, shot_name: str, from_search: bool = False) -> RowResult:
    status = session.open(url, READY_SELECTOR)
    if status != "ok":
        row.status = "failed"
        return row
    page = session.page
    row.page_title = session.title()
    log.info("[%s] title: %s", row.id, row.page_title)
    _ensure_new_tab(page)
    try:
        offers, idx = extract_offers(page)
    except Exception as e:  # noqa: BLE001
        log.exception("[%s] extractor error", row.id)
        offers, idx = [], []
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
    shot = session.screenshot_element(page.locator(f"[data-pricecheck-row='{idx[i]}']").first, shot_name)
    if shot:
        row.screenshots.append(shot)
    log.info("[%s] decision: %s at %s (%s) %s", row.id, offer.price, offer.shop, offer.stock_text,
             offer.price_note or "")
    return row


def extract_tiles(page) -> list[Tile]:
    res = page.evaluate(_JS_TILES, {"tile_selectors": TILE_SELECTORS, "name_selectors": TILE_NAME_SELECTORS,
                                    "href_part": PRODUCT_HREF_PART})
    log.info("idealo search: tile detection method: %s, %d tiles", res["method"], len(res["tiles"]))
    return [t for t in (parse_tile(r, page.url) for r in res["tiles"]) if t.text]


def read_search(session, row: RowResult, url: str, check) -> RowResult:
    """F2: walk the price-sorted tiles, take the first new product that matches `check`."""
    status = session.open(url, ", ".join(TILE_SELECTORS) + f", a[href*='{PRODUCT_HREF_PART}']")
    if status != "ok":
        row.status = "failed"
        return row
    page = session.page
    row.page_title = session.title()
    tiles = extract_tiles(page)
    if not tiles:
        row.status = "captcha" if session.is_captcha() else "no_price"
        session.dump_debug(row.id)
        return row
    tiles.sort(key=lambda t: (t.price is None, t.price or Decimal(0), t.index))
    chosen = None
    for t in tiles:
        if t.used:
            ok, reason = False, "used"
        else:
            ok, reason = check(t)
        log.info("[%s]   %s | %s | %s", row.id, t.price, t.name[:80], "MATCH" if ok else f"skip: {reason}")
        if ok:
            chosen = t
            break
        row.skipped.append((t.name, reason))
    if chosen is None:
        row.status = "no_match"
        return row
    row.product, row.product_url = chosen.name, chosen.href
    try:
        card = page.locator("body").evaluate_handle(
            "(b, i) => window.__pricecheckTiles && window.__pricecheckTiles[i]", chosen.index)
        el = card.as_element()
        if el:
            path = session.run_dir / f"{row.id}_card.png"
            el.scroll_into_view_if_needed(timeout=3000)
            el.screenshot(path=str(path), timeout=10_000)
            row.screenshots.append(str(path))
    except Exception as e:  # noqa: BLE001
        log.warning("[%s] card screenshot failed: %s", row.id, e)
    if chosen.is_product_page:
        read_product(session, row, chosen.href, f"{row.id}_offer.png", from_search=True)
        if not row.ok and chosen.price is not None and row.status in ("no_price", "failed"):
            row.notes.append(f"product page: {row.status_text()}; price taken from the search tile")
            row.price, row.status = chosen.price, "ok"
    else:
        # Single marketplace offer tile: no idealo product page to open, use the tile itself.
        seller = SELLER_RE.search(chosen.raw_text)
        row.price = chosen.price
        row.shop = clean(seller.group(1)) if seller else None
        row.status = "ok" if chosen.price is not None else "no_price"
        row.notes.append("single marketplace offer, read from the search tile")
    return row


def ram_tile_check(tile: Tile):
    from ..specs import check_ram
    return check_ram(tile.name, tile.text)
