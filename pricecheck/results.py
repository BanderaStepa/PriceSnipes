"""Result records produced by the site readers and consumed by the report."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal

# Row status -> text shown in the "Lowest price" cell when there is no price.
STATUS_TEXT = {
    "ok": "",
    "failed": "page failed to load",
    "no_price": "no price found",
    "captcha": "blocked by site (captcha)",
    "wrong_variant": "wrong variant loaded",
    "no_match": "no matching product on results page",
    "skipped": "not checked (--only)",
}
# Statuses that count as "could not be read" for the red warning line.
FAILED_STATUSES = {"failed", "no_price", "captcha", "wrong_variant"}


@dataclass
class Offer:
    """One parsed offer row on a product page."""
    price: Decimal | None
    shop: str | None = None
    in_stock: bool | None = None
    stock_text: str | None = None
    price_note: str | None = None   # e.g. "(price only with voelkner+)"
    raw_text: str = ""


@dataclass
class RowResult:
    id: str
    status: str = "ok"
    price: Decimal | None = None
    shop: str | None = None
    in_stock: bool | None = None
    stock_text: str | None = None
    price_note: str | None = None
    page_title: str | None = None
    product: str | None = None        # filters: name of the chosen product
    product_url: str | None = None    # filters: link to the chosen product
    screenshots: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)   # filters: (name, reason)
    offers_seen: int = 0
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.price is not None

    def status_text(self) -> str:
        return STATUS_TEXT.get(self.status, self.status)

    def to_json(self) -> dict:
        d = asdict(self)
        d["price"] = str(self.price) if self.price is not None else None
        d["skipped"] = [list(x) for x in self.skipped]
        return d


def apply_offer(row: RowResult, offer: Offer) -> RowResult:
    row.price = offer.price
    row.shop = offer.shop
    row.in_stock = offer.in_stock
    row.stock_text = offer.stock_text
    row.price_note = offer.price_note
    row.status = "ok" if offer.price is not None else "no_price"
    return row
