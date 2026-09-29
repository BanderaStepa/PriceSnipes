from decimal import Decimal

import pytest

from pricecheck.parse import classify_stock, find_prices, parse_price, stock_label


@pytest.mark.parametrize("text,expected", [
    ("€ 1556,40", "1556.40"),
    ("€ 1.556,40", "1556.40"),
    ("1.556,40 €", "1556.40"),
    ("€271.54", "271.54"),
    ("€1,556.40", "1556.40"),
    ("€ 76,38", "76.38"),
    ("519,90 € inkl. Versand", "519.90"),
    ("-32 % 50,51 €", "50.51"),
    ("ab479,00 €", "479.00"),
    ("8 Angebote ab € 445,26", "445.26"),
    ("€ 111,07 (€ 13,91 / GB)", "111.07"),
    ("(€ 13,91 / GB) € 111,07", "111.07"),
])
def test_parse_price(text, expected):
    assert parse_price(text) == Decimal(expected)


def test_per_unit_price_ignored():
    assert parse_price("(€ 13,91 / GB)") is None
    assert find_prices("(€ 13,91/GB) 13,91 €/Stück") == []


def test_returns_decimal_two_places():
    p = parse_price("€ 1556,4")
    assert isinstance(p, Decimal) and str(p) == "1556.40"


@pytest.mark.parametrize("text,expected", [
    ("Auf Lager, 1-2 Werktage", True),
    ("4-10 Arbeitstage", False),
    ("Bestellt, wird in 2 Werktagen erwartet", False),
    ("Nicht lagernd, ab Bestellung verfügbar in 7-13 Werktagen", False),
    ("Lagernd im Außenlager, Lieferung 2-3 Werktage", True),
    ("Nur noch 1 auf Lager", True),
    # further rules from §3
    ("sofort lieferbar, Lieferzeit 1-3 Werktage", True),
    ("Lieferzeit 1-2 Werktage", True),
    ("Lieferzeit 5-7 Werktage", False),
    ("7-13 Werktagen", False),
    ("Derzeit nicht verfügbar.", False),
    ("In stock", True),
    ("sofort verfügbar", True),
    ("Ab Lager lieferbar", True),
])
def test_stock_rules(text, expected):
    assert classify_stock(text)[0] is expected


def test_idealo_dot_colour():
    assert classify_stock("Lieferung: bis Mi. 30.09.", dot="green")[0] is True
    assert classify_stock("bis Mo. 12.10.", dot="yellow")[0] is False
    assert classify_stock("bis Mo. 12.10.", dot="orange")[0] is False
    assert classify_stock("bis Mo. 12.10.")[0] is None
    # explicit negative text beats a green dot
    assert classify_stock("nicht lagernd", dot="green")[0] is False


def test_stock_label_keeps_shop_text():
    ok, txt = classify_stock("Auf Lager, 1-2 Werktage")
    assert stock_label(ok, txt) == "Yes (Auf Lager, 1-2 Werktage)"
    ok, txt = classify_stock("4-10 Arbeitstage")
    assert stock_label(ok, txt) == "No (4-10 Arbeitstage)"


def test_cheapest_prefers_known_shop_on_tie():
    from pricecheck.results import Offer
    from pricecheck.sites.common import cheapest
    offers = [Offer(Decimal("519.90"), "unknown shop"), Offer(Decimal("519.90"), "GALAXUS"), Offer(Decimal("530"), "A")]
    assert cheapest(offers)[0] == 1
    offers = [Offer(Decimal("520"), "A"), Offer(Decimal("519.90"), "B"), Offer(Decimal("519.90"), "C")]
    assert cheapest(offers)[1].shop == "B"
