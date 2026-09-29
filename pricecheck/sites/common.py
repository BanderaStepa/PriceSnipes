"""Helpers shared by the site extractors."""
from __future__ import annotations

import logging
import re

from ..parse import short_stock_text
from ..results import Offer

log = logging.getLogger(__name__)

# Where an availability phrase starts inside a longer text ...
AVAIL_START_RE = re.compile(
    r"nur\s+noch\s+\d+\s+auf\s+lager|nicht\s+lagernd|nicht\s+auf\s+lager|auf\s+lager|\blagernd\b|"
    r"sofort\s+lieferbar|sofort\s+verf[üu]gbar|ab\s+lager\s+lieferbar|\bbestellt\b|"
    r"nicht\s+verf[üu]gbar|derzeit\s+nicht|lieferzeit|lieferung|\blieferbar\b|"
    r"\d{1,2}\s*[-–]\s*\d{1,2}\s*(?:arbeits|werk)tag|\d{1,2}\s*(?:arbeits|werk)tag|"
    r"in\s+stock|out\s+of\s+stock|available\s+from\s+stock|currently\s+unavailable",
    re.I,
)
# ... and where it ends (payment methods, shipping costs, prices).
AVAIL_END_RE = re.compile(
    r"vorkasse|nachnahme|kreditkarte|paypal|\brechnung\b|lastschrift|sofortüberweisung|klarna|"
    r"gratisversand|versandkosten|\bversand\b|€|\bpayment\b|credit\s+card|\bshipping\b",
    re.I,
)

PAYMENT_WORDS_RE = re.compile(
    r"paypal|\bvisa\b|master\s?card|kreditkarte|american express|amex|rechnung|vorkasse|nachnahme|"
    r"lastschrift|sofort|klarna|amazon\s?pay|apple\s?pay|google\s?pay|giropay|überweisung|"
    r"ratenkauf|finanzierung|bitcoin|payback|bewertung|sterne|siegel|trusted|testsieger|idealo|"
    r"versand|\bdhl\b|hermes|\bdpd\b|\bups\b|\bgls\b",
    re.I,
)
MARKETPLACE_RE = re.compile(
    r"(?:via|über)\s+(Amazon(?:\.de)?\s+Marketplace|eBay|Kaufland(?:\.de)?|Otto\s+Market(?:place)?)"
    r"|\b(Amazon(?:\.de)?|eBay|Kaufland(?:\.de)?)[\s.-]*Marketplace\b",
    re.I,
)


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def extract_availability(text: str) -> str:
    """Pull the shop's short availability phrase out of a cell or row text."""
    if not text:
        return ""
    flat = re.sub(r"\s*\n\s*", ", ", text.strip())
    flat = re.sub(r"[ \t ]+", " ", flat)
    m = AVAIL_START_RE.search(flat)
    if not m:
        return ""
    rest = flat[m.start():]
    e = AVAIL_END_RE.search(rest, 3)
    if e:
        rest = rest[: e.start()]
    return short_stock_text(rest.strip(" ,;·|-"))


def marketplace_note(text: str, shop: str | None) -> str | None:
    m = MARKETPLACE_RE.search(text or "")
    if not m:
        return shop
    mk = clean(m.group(1) or f"{m.group(2)} Marketplace")
    if shop and mk.lower() in shop.lower():
        return shop
    if not shop:
        return mk
    return f"{shop} (via {mk})"


def cheapest(offers: list[Offer]) -> tuple[int, Offer] | None:
    """(index, offer) with the lowest price. On a tie the earliest row with a known shop wins."""
    best = None
    for i, o in enumerate(offers):
        if o.price is None:
            continue
        if best is None or o.price < best[1].price:
            best = (i, o)
        elif o.price == best[1].price and _unknown(best[1].shop) and not _unknown(o.shop):
            best = (i, o)
    return best


def _unknown(shop: str | None) -> bool:
    return not shop or shop == "unknown shop"


def log_offers(row_id: str, offers: list[Offer], limit: int = 8) -> None:
    log.info("[%s] parsed %d offer rows", row_id, len(offers))
    for o in offers[:limit]:
        log.info("[%s]   %s | %s | %s | %s%s", row_id, o.price, o.shop, o.in_stock, o.stock_text,
                 f" | {o.price_note}" if o.price_note else "")
