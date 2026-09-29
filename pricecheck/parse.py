"""Price and stock parsing shared by all site extractors.

Everything here is pure text processing so it can be unit-tested without a browser.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

# --------------------------------------------------------------------------- prices

# A number as shops print it: 1556,40 / 1.556,40 / 1,556.40 / 271.54 / 76,38 / 1556
_NUM = r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?![\d])"
_WS = r"[\s  ]*"
# "€ 1.556,40" / "€1,556.40"   or   "1.556,40 €" / "50,51€"
PRICE_RE = re.compile(rf"€{_WS}{_NUM}|{_NUM}{_WS}€")
# A price directly followed by "/ GB", "/GB", "/ 1 GB", "/Stück" ... is a per-unit price.
_PER_UNIT_AFTER = re.compile(rf"{_WS}/")


def normalize_number(num: str) -> Decimal:
    """Turn '1.556,40', '1,556.40', '1556,40', '271.54', '1.556' into a Decimal."""
    num = num.strip()
    last_sep = max(num.rfind("."), num.rfind(","))
    if last_sep == -1:
        return Decimal(num)
    decimals = len(num) - last_sep - 1
    if decimals == 3:
        # Only thousands separators, e.g. "1.556" or "1,556".
        return Decimal(re.sub(r"[.,]", "", num))
    whole = re.sub(r"[.,]", "", num[:last_sep])
    return Decimal(f"{whole}.{num[last_sep + 1:]}")


def _q(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def find_prices(text: str) -> list[Decimal]:
    """All € prices in `text`, in order of appearance, per-unit prices skipped."""
    if not text:
        return []
    out: list[Decimal] = []
    for m in PRICE_RE.finditer(text):
        if _PER_UNIT_AFTER.match(text, m.end()):
            continue  # "(€ 13,91 / GB)"
        num = m.group(1) or m.group(2)
        try:
            out.append(_q(normalize_number(num)))
        except Exception:  # pragma: no cover - defensive
            continue
    return out


def find_price_matches(text: str) -> list[tuple[Decimal, int, int]]:
    """Like find_prices but also returns (start, end) positions of each match."""
    out = []
    for m in PRICE_RE.finditer(text or ""):
        if _PER_UNIT_AFTER.match(text, m.end()):
            continue
        num = m.group(1) or m.group(2)
        out.append((_q(normalize_number(num)), m.start(), m.end()))
    return out


def parse_price(text: str) -> Decimal | None:
    """First € price in `text` (per-unit prices ignored), or None."""
    prices = find_prices(text)
    return prices[0] if prices else None


# --------------------------------------------------------------------------- stock

NEGATIVE_STOCK = [
    r"nicht\s+lagernd",
    r"nicht\s+auf\s+lager",
    r"nicht\s+verf[üu]gbar",
    r"derzeit\s+nicht",
    r"\bbestellt\b",
    r"\bwird\b.{0,40}?\berwartet\b",
    r"out\s+of\s+stock",
    r"not\s+in\s+stock",
    r"currently\s+unavailable",
    r"temporarily\s+out",
]
POSITIVE_STOCK = [
    r"nur\s+noch\s+\d+\s+auf\s+lager",
    r"auf\s+lager",
    r"\blagernd\b",
    r"sofort\s+lieferbar",
    r"sofort\s+verf[üu]gbar",
    r"ab\s+lager\s+lieferbar",
    r"\bin\s+stock\b",
    r"available\s+from\s+stock",
    r"only\s+\d+\s+left\s+in\s+stock",
]
# "4-10 Arbeitstage", "Lieferzeit 5-7 Werktage", "7-13 Werktagen", "2 Werktage", "1-3 business days"
DELIVERY_RANGE_RE = re.compile(
    r"(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\s*(?:arbeits|werk)?tag(?:e|en)?\b|"
    r"(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\s*(?:business|working)?\s*days?\b",
    re.I,
)
# A plain delivery range whose upper end is at most this many days counts as "in stock"
# (spec: "Lieferzeit 1-2 Werktage" without a stock word is in stock; 4-10 / 5-7 / 7-13 are not).
FAST_DELIVERY_MAX_DAYS = 3

_NEG = [re.compile(p, re.I) for p in NEGATIVE_STOCK]
_POS = [re.compile(p, re.I) for p in POSITIVE_STOCK]


def short_stock_text(text: str, limit: int = 70) -> str:
    t = re.sub(r"\s+", " ", text or "").strip(" ,;·|")
    if len(t) > limit:
        t = t[: limit - 1].rstrip() + "…"
    return t


def classify_stock(text: str, dot: str | None = None) -> tuple[bool | None, str]:
    """Return (in_stock, short text).

    in_stock is True / False, or None when the text gives no usable signal.
    `dot` is an optional colour of an availability bullet ("green", "yellow", "orange", "red")
    as detected on idealo; it wins over the delivery-date text but not over explicit
    "nicht lagernd"-style phrases.
    """
    short = short_stock_text(text)
    t = re.sub(r"\s+", " ", text or "").lower()
    if any(r.search(t) for r in _NEG):
        return False, short
    if dot == "green":
        return True, short
    if dot in ("yellow", "orange", "red"):
        return False, short
    if any(r.search(t) for r in _POS):
        return True, short
    ranges = []
    for m in DELIVERY_RANGE_RE.finditer(t):
        lo, hi = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        if lo:
            ranges.append(int(hi or lo))
    if ranges:
        return (max(ranges) <= FAST_DELIVERY_MAX_DAYS), short
    return None, short


def stock_label(in_stock: bool | None, text: str | None) -> str:
    """'Yes (Auf Lager, 1-2 Werktage)' / 'No (4-10 Arbeitstage)' / '? (…)'."""
    word = {True: "Yes", False: "No", None: "?"}[in_stock]
    if text:
        return f"{word} ({text})"
    return word if in_stock is not None else "—"
