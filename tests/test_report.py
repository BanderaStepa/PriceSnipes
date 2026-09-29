import re
from decimal import Decimal

from pricecheck import report
from pricecheck.report import MINUS, fmt_eur, fmt_signed
from pricecheck.results import RowResult

GREEN = "background:#d4edda;color:#155724;font-weight:bold"
RED = "background:#f8d7da;color:#721c24;font-weight:bold"


def _m(s):  # "−12.60" helper
    return s.replace("-", MINUS)


def test_formatting():
    assert fmt_eur(Decimal("1556.40")) == "€1,556.40"
    assert fmt_signed(Decimal("0.49")) == "+0.49"
    assert fmt_signed(Decimal("-12.60")) == _m("-12.60")
    assert fmt_signed(Decimal("0")) == "0.00"


def test_differences_2026_09_29(config, results_0929, now_kyiv):
    rep = report.compute(config, results_0929, now_kyiv)
    assert [r.cfg["id"] for r in rep.rows] == ["1", "2", "3", "4", "5", "6a", "6b", "6c", "6d", "7a", "7b", "8a", "8b"]
    diffs = [fmt_signed(r.diff) for r in rep.rows]
    assert diffs == [_m(x) for x in
                     ["+0.49", "-12.60", "-1.85", "-3.83", "0.00", "0.00", "0.00", "+7.99", "+7.99", "0.00",
                      "-0.11", "+1.68", "-0.01"]]


def test_totals_2026_09_29(config, results_0929, now_kyiv):
    rep = report.compute(config, results_0929, now_kyiv)
    assert rep.total == Decimal("2830.92")
    assert rep.total_diff == Decimal("-17.80")
    parts = ", ".join(rep.total_parts)
    assert "RAM = 6a/6b (tie €519.90)" in parts
    assert "Cooler = 7a (€50.51)" in parts
    assert "Case = 8b (€91.13)" in parts
    assert rep.alt_total == Decimal("2798.01")
    assert rep.alt_diff == Decimal("-50.71")
    assert rep.purchases_line is None
    assert [r.cfg["id"] for r in rep.drops] == ["2", "3", "4", "7b", "8b"]


def test_filters_2026_09_29(config, results_0929, now_kyiv):
    rep = report.compute(config, results_0929, now_kyiv)
    assert [fmt_signed(f.diff) for f in rep.filter_rows] == [_m(x) for x in ["+16.26", "+50.00", "-64.16"]]
    assert rep.compare_ram[0] == Decimal("-74.64")
    assert rep.compare_gpu[0] == Decimal("-162.66")
    assert [f.cfg["id"] for f in rep.filter_drops] == ["F3"]
    assert "CL36 ×1" in rep.skipped_line and "CL48 ×1" in rep.skipped_line


def test_purchase_math(config, results_0929, now_kyiv):
    config["purchases"] = [{"item_group": "2", "price": 1540.00, "shop": "Mindfactory", "date": "2026-10-02"}]
    rep = report.compute(config, results_0929, now_kyiv)
    row2 = next(r for r in rep.rows if r.cfg["id"] == "2")
    assert row2.saved == Decimal("29.00")
    assert row2.saved_pct == Decimal("1.8")
    assert rep.total == Decimal("2830.92") - Decimal("1556.40") + Decimal("1540.00")
    html = report.render_html(rep)
    assert "€1,540.00" in html and "€29.00" in html and "1.8%" in html
    assert rep.purchases_line and "€1,540.00 spent" in rep.purchases_line and "Still to buy" in rep.purchases_line
    # the item is still checked and its difference still shown
    assert fmt_signed(row2.diff) == _m("-12.60")
    # other rows are not marked as bought
    row1 = rep.rows[0]
    assert row1.purchase is None


def test_group_purchase_fills_all_rows(config, results_0929, now_kyiv):
    config["purchases"] = [{"item_group": "CASE", "price": 90.00, "shop": "X", "date": "2026-10-01"}]
    rep = report.compute(config, results_0929, now_kyiv)
    bought = [r.cfg["id"] for r in rep.rows if r.purchase]
    assert bought == ["8a", "8b"]
    assert all(r.saved == Decimal("1.14") for r in rep.rows if r.purchase)


def test_failed_rows_and_base_fallback(config, results_0929, now_kyiv):
    results_0929["2"] = RowResult(id="2", status="failed")
    results_0929["6a"] = RowResult(id="6a", status="captcha")
    rep = report.compute(config, results_0929, now_kyiv)
    row2 = rep.rows[1]
    assert row2.diff is None
    assert "GPU = €1,569.00 (no current price, base used)" in rep.total_parts
    assert "RAM = 6b (€519.90)" in rep.total_parts
    assert rep.total == Decimal("2830.92") - Decimal("1556.40") + Decimal("1569.00")
    html = report.render_html(rep)
    assert "⚠ 2 rows could not be read: 2 (page failed to load), 6a (blocked by site (captcha))" in html
    assert "page failed to load" in html


def test_subject(config, results_0929, now_kyiv):
    rep = report.compute(config, results_0929, now_kyiv)
    assert rep.subject == f"White Build prices – 2026-09-29 12:15 Kyiv – total €2,830.92 ({MINUS}€17.80 vs base)"


def test_html_colours(config, results_0929, now_kyiv):
    rep = report.compute(config, results_0929, now_kyiv)
    html = report.render_html(rep)

    def cell_style(value):
        m = re.search(r'<td style="([^"]*)">' + re.escape(value) + "</td>", html)
        assert m, value
        return m.group(1)

    assert GREEN in cell_style(_m("-12.60"))
    assert GREEN in cell_style(_m("-64.16"))
    assert RED in cell_style("+0.49")
    assert RED in cell_style("+16.26")
    zero = re.findall(r'<td style="([^"]*)">0\.00</td>', html)
    assert len(zero) == 4 and all(GREEN not in s and RED not in s for s in zero)
    # totals / drop lists / comparisons carry the colour too
    assert f'<span style="{GREEN};padding:0 3px">{MINUS}€17.80</span>' in html
    assert f'<span style="{GREEN};padding:0 3px">{MINUS}74.64</span>' in html
    assert "<style" not in html


def test_text_report(config, results_0929, now_kyiv):
    rep = report.compute(config, results_0929, now_kyiv)
    txt = report.render_text(rep)
    assert "Current total: €2,830.92" in txt
    assert f"{MINUS}162.66" in txt


def test_link_labels_use_hostname(config, results_0929, now_kyiv):
    html = report.render_html(report.compute(config, results_0929, now_kyiv))
    assert html.count(">amazon</a>") == 1       # the amazon URL carries an idealo affiliate tag
    assert html.count(">idealo</a>") == 3
    assert html.count(">geizhals</a>") == 9


def test_single_failed_row_wording(config, results_0929, now_kyiv):
    results_0929["6a"] = RowResult(id="6a", status="captcha")
    txt = report.render_text(report.compute(config, results_0929, now_kyiv))
    assert txt.startswith("⚠ 1 row could not be read: 6a (blocked by site (captcha))")
