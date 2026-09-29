"""Report math and rendering (HTML with inline styles for Gmail, plus plain text).

Bases always come from config.json; nothing is ever compared with a previous run.
"""
from __future__ import annotations

import html
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from urllib.parse import urlparse

from .parse import stock_label
from .results import FAILED_STATUSES, RowResult

MINUS = "−"  # typographic minus, as in "−12.60"
GREEN = "background:#d4edda;color:#155724;font-weight:bold"
RED = "background:#f8d7da;color:#721c24;font-weight:bold"
WARN = "color:#b00020;font-weight:bold"
TABLE = "border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;font-size:13px"
TH = "border:1px solid #bbb;padding:4px 6px;background:#f0f0f0;text-align:left"
TD = "border:1px solid #ccc;padding:4px 6px;vertical-align:top"
DASH = "—"

MAIN_COLUMNS = ["Item", "Lowest price", "Shop", "In stock", "Base", "Difference in € (negative = cheaper)",
                "Bought at", "Saved €", "Saved %", "Link"]
FILTER_COLUMNS = ["Filter", "Lowest price", "Product", "Shop", "In stock", "Base",
                  "Difference in € (negative = cheaper)", "Link"]
FILTER_TITLE = "Spec filter links (cheapest part with the required specs, any brand)"


# ----------------------------------------------------------------------------- formatting
def D(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt_eur(d: Decimal | None) -> str:
    if d is None:
        return DASH
    s = f"€{abs(d):,.2f}"
    return f"{MINUS}{s}" if d < 0 else s


def fmt_signed(d: Decimal | None) -> str:
    """+0.49 / −12.60 / 0.00"""
    if d is None:
        return DASH
    d = D(d)
    if d > 0:
        return f"+{d:,.2f}"
    if d < 0:
        return f"{MINUS}{abs(d):,.2f}"
    return "0.00"


def fmt_eur_signed(d: Decimal) -> str:
    """+€0.49 / −€17.80 / €0.00"""
    d = D(d)
    if d > 0:
        return f"+€{d:,.2f}"
    if d < 0:
        return f"{MINUS}€{abs(d):,.2f}"
    return "€0.00"


def diff_style(d: Decimal | None) -> str:
    if d is None:
        return ""
    d = D(d)
    return GREEN if d < 0 else RED if d > 0 else ""


def styled_span(text: str, d: Decimal | None) -> str:
    st = diff_style(d)
    return f'<span style="{st};padding:0 3px">{html.escape(text)}</span>' if st else html.escape(text)


def short_name(name: str, words: int = 4) -> str:
    parts = (name or "").split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


# ----------------------------------------------------------------------------- data
@dataclass
class MainRow:
    cfg: dict
    res: RowResult
    base: Decimal
    diff: Decimal | None
    purchase: dict | None
    saved: Decimal | None
    saved_pct: Decimal | None


@dataclass
class FilterRow:
    cfg: dict
    res: RowResult
    base: Decimal
    diff: Decimal | None


@dataclass
class Report:
    now: datetime
    tz_label: str
    base_total: Decimal
    rows: list[MainRow]
    total: Decimal
    total_diff: Decimal
    total_parts: list[str]
    alt_label: str | None
    alt_total: Decimal | None
    alt_diff: Decimal | None
    alt_parts: list[str]
    alt_note: str | None
    purchases_line: str | None
    drops: list[MainRow]
    filter_rows: list[FilterRow]
    filter_drops: list[FilterRow]
    skipped_line: str
    compare_ram: tuple[Decimal, Decimal, str, str] | None     # (diff, spec price, which filter, which link)
    compare_gpu: tuple[Decimal, Decimal, Decimal] | None       # (diff, F3 price, item 2 price)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def subject(self) -> str:
        return (f"White Build prices – {self.now:%Y-%m-%d %H:%M} {self.tz_label} – "
                f"total {fmt_eur(self.total)} ({fmt_eur_signed(self.total_diff)} vs base)")


def _group_key(item: dict) -> str:
    return item.get("group") or item["id"]


def compute(config: dict, results: dict[str, RowResult], now: datetime) -> Report:
    items = config["items"]
    by_group: "OrderedDict[str, list[dict]]" = OrderedDict()
    for it in items:
        by_group.setdefault(_group_key(it), []).append(it)
    purchases = {p["item_group"]: p for p in config.get("purchases", [])}

    def res_of(item_id: str) -> RowResult:
        return results.get(item_id) or RowResult(id=item_id, status="skipped")

    # ---- main rows
    rows: list[MainRow] = []
    for it in items:
        r = res_of(it["id"])
        base = D(it["base"])
        diff = D(r.price - base) if r.ok else None
        p = purchases.get(_group_key(it)) or purchases.get(it["id"])
        saved = pct = None
        if p:
            saved = D(base - D(p["price"]))
            pct = (saved / base * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        rows.append(MainRow(it, r, base, diff, p, saved, pct))

    # ---- totals
    def group_value(key: str) -> tuple[Decimal, str | None, bool]:
        """(value, description for the total line, is_current_price_or_bought)"""
        members = by_group.get(key, [])
        label = members[0]["item"] if members else key
        p = purchases.get(key)
        if p:
            return D(p["price"]), f"{label} = bought {fmt_eur(D(p['price']))}", True
        readable = [(m["id"], res_of(m["id"]).price) for m in members if res_of(m["id"]).ok]
        if not readable:
            base = min(D(m["base"]) for m in members)
            return base, f"{label} = {fmt_eur(base)} (no current price, base used)", False
        low = min(pr for _, pr in readable)
        ids = [i for i, pr in readable if pr == low]
        if len(members) == 1:
            return low, None, True
        tie = "tie " if len(ids) > 1 else ""
        return low, f"{label} = {'/'.join(ids)} ({tie}{fmt_eur(low)})", True

    swap = config.get("alt_total_swap") or {}
    groups = list(config["build_total_groups"])
    alt_note = None
    swapped = False
    if swap and swap.get("to") in purchases and swap.get("from") not in purchases and swap.get("from") in groups:
        groups = [swap["to"] if g == swap["from"] else g for g in groups]
        swapped = True

    def total_for(gs: list[str]) -> tuple[Decimal, list[str]]:
        tot, parts = Decimal("0.00"), []
        for g in gs:
            v, desc, _ = group_value(g)
            tot += v
            if desc:
                parts.append(desc)
        return D(tot), parts

    base_total = D(config["base_total"])
    total, total_parts = total_for(groups)
    total_diff = D(total - base_total)

    alt_total = alt_diff = None
    alt_parts: list[str] = []
    alt_label = swap.get("label") if swap else None
    if swap:
        if swapped:
            alt_note = f"{by_group[swap['to']][0]['item']} bought: it is used in the current total above."
        elif swap.get("from") in purchases:
            alt_note = f"{by_group[swap['from']][0]['item']} already bought, no alternative total."
        else:
            alt_groups = [swap["to"] if g == swap["from"] else g for g in config["build_total_groups"]]
            alt_total, parts = total_for(alt_groups)
            alt_diff = D(alt_total - base_total)
            v, desc, _ = group_value(swap["to"])
            alt_parts = [desc] if desc else []

    # ---- purchases summary
    purchases_line = None
    bought_groups = [g for g in groups if g in purchases]
    extra_bought = [g for g in purchases if g not in groups]
    if purchases:
        spent = sum((D(purchases[g]["price"]) for g in purchases), Decimal("0.00"))
        bases = Decimal("0.00")
        for g in purchases:
            members = by_group.get(g, [])
            if members:
                bases += min(D(m["base"]) for m in members)
        saved = D(bases - spent)
        pct = (saved / bases * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if bases else Decimal("0.0")
        left = [by_group[g][0]["item"] for g in groups if g not in purchases and g in by_group]
        what = ", ".join(by_group[g][0]["item"] for g in list(bought_groups) + extra_bought if g in by_group)
        purchases_line = (f"Bought so far ({what}): {fmt_eur(spent)} spent, saved {fmt_eur_signed(saved)} "
                          f"({'+' if pct > 0 else MINUS if pct < 0 else ''}{abs(pct)}%) against the bases of the "
                          f"bought items. Still to buy: {', '.join(left) if left else 'nothing'}.")

    drops = [r for r in rows if r.diff is not None and r.diff < 0]

    # ---- filters
    filter_rows = []
    for f in config.get("filters", []):
        r = res_of(f["id"])
        base = D(f["base"])
        filter_rows.append(FilterRow(f, r, base, D(r.price - base) if r.ok else None))
    filter_drops = [f for f in filter_rows if f.diff is not None and f.diff < 0]

    skipped_parts = []
    for fr in filter_rows:
        if not fr.res.skipped:
            continue
        by_reason: "OrderedDict[str, list[str]]" = OrderedDict()
        for name, reason in fr.res.skipped:
            by_reason.setdefault(reason or "?", []).append(name)
        chunks = []
        for reason, names in by_reason.items():
            ex = ", ".join(short_name(n) for n in names[:2]) + (", …" if len(names) > 2 else "")
            chunks.append(f"{reason} ×{len(names)} ({ex})")
        skipped_parts.append(f"{fr.cfg['id']}: " + "; ".join(chunks))
    skipped_line = ("Skipped non-matching hits — " + " | ".join(skipped_parts)) if skipped_parts else \
        "Skipped non-matching hits — none."

    # ---- comparisons
    def lowest(ids: list[str]) -> tuple[Decimal, str] | None:
        vals = [(res_of(i).price, i) for i in ids if res_of(i).ok]
        if not vals:
            return None
        low = min(v for v, _ in vals)
        return low, "/".join(i for v, i in vals if v == low)

    ram_filter_ids = [f["id"] for f in config.get("filters", []) if f.get("kind") == "ram"]
    gpu_filter_ids = [f["id"] for f in config.get("filters", []) if f.get("kind") == "gpu"]
    patriot_ids = [m["id"] for m in by_group.get(swap.get("from") if swap else "RAM_PATRIOT", [])]
    spec_ram, patriot = lowest(ram_filter_ids), lowest(patriot_ids)
    compare_ram = None
    if spec_ram and patriot:
        compare_ram = (D(spec_ram[0] - patriot[0]), spec_ram[0], spec_ram[1], f"{patriot[1]} {fmt_eur(patriot[0])}")
    compare_gpu = None
    spec_gpu, item2 = lowest(gpu_filter_ids), lowest(["2"])
    if spec_gpu and item2:
        compare_gpu = (D(spec_gpu[0] - item2[0]), spec_gpu[0], item2[0])

    failed = [(r.res.id, r.res.status_text()) for r in rows if r.res.status in FAILED_STATUSES]
    failed += [(f.res.id, f.res.status_text()) for f in filter_rows if f.res.status in FAILED_STATUSES]

    tz_label = config.get("timezone", "Europe/Kyiv").split("/")[-1]
    return Report(now=now, tz_label=tz_label, base_total=base_total, rows=rows, total=total, total_diff=total_diff,
                  total_parts=total_parts, alt_label=alt_label, alt_total=alt_total, alt_diff=alt_diff,
                  alt_parts=alt_parts, alt_note=alt_note, purchases_line=purchases_line, drops=drops,
                  filter_rows=filter_rows, filter_drops=filter_drops, skipped_line=skipped_line,
                  compare_ram=compare_ram, compare_gpu=compare_gpu, failed=failed)


# ----------------------------------------------------------------------------- cell helpers
def _price_cell(res: RowResult) -> str:
    if not res.ok:
        return res.status_text()
    s = fmt_eur(res.price)
    if res.price_note:
        s += f" {res.price_note}"
    return s


def _stock_cell(res: RowResult) -> str:
    if not res.ok:
        return DASH
    return stock_label(res.in_stock, res.stock_text)


def _site_name(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    for s in ("geizhals", "idealo", "amazon"):
        if s in host:
            return s
    return "link"


def _failed_line(failed: list[tuple[str, str]]) -> str:
    n = len(failed)
    lst = ", ".join(f"{i} ({t})" for i, t in failed)
    return f"⚠ {n} {'row' if n == 1 else 'rows'} could not be read: {lst}"


def _a(url: str | None, text: str) -> str:
    if not url:
        return html.escape(text)
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(text)}</a>'


def _td(content_html: str, style: str = "") -> str:
    return f'<td style="{TD}{";" + style if style else ""}">{content_html}</td>'


def _bought_cells(r: MainRow) -> tuple[str, str, str]:
    if not r.purchase:
        return DASH, DASH, DASH
    p = r.purchase
    extra = ", ".join(x for x in (p.get("shop"), p.get("date")) if x)
    bought = fmt_eur(D(p["price"])) + (f" ({extra})" if extra else "")
    pct = r.saved_pct
    pct_s = f"{MINUS}{abs(pct)}%" if pct < 0 else f"{pct}%"
    return bought, fmt_eur(r.saved), pct_s


def _item_label(cfg: dict) -> str:
    return f"{cfg['id']} {cfg['item']}: {cfg['name']}"


# ----------------------------------------------------------------------------- HTML
def render_html(rep: Report, screenshots: list[tuple[str, str]] | None = None) -> str:
    """Email/report body. `screenshots` = [(title, relative path)] embeds images (local report only)."""
    out = ['<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222">']
    if rep.failed:
        out.append(f'<p style="{WARN}">{html.escape(_failed_line(rep.failed))}</p>')
    out.append(f'<h2 style="font-size:18px;margin:8px 0">White Build prices – {rep.now:%Y-%m-%d %H:%M} '
               f'{html.escape(rep.tz_label)}</h2>')

    # main table
    out.append(f'<table style="{TABLE}"><tr>' + "".join(f'<th style="{TH}">{html.escape(c)}</th>' for c in MAIN_COLUMNS) + "</tr>")
    for r in rep.rows:
        res = r.res
        bought, saved, pct = _bought_cells(r)
        cells = [
            _td(html.escape(_item_label(r.cfg))),
            _td(html.escape(_price_cell(res)), "white-space:nowrap"),
            _td(html.escape(res.shop or DASH) if res.ok else DASH, "min-width:90px"),
            _td(html.escape(_stock_cell(res))),
            _td(fmt_eur(r.base), "white-space:nowrap"),
            _td(fmt_signed(r.diff), (diff_style(r.diff) + ";white-space:nowrap").lstrip(";")),
            _td(html.escape(bought)),
            _td(html.escape(saved)),
            _td(html.escape(pct)),
            _td(_a(r.cfg["url"], _site_name(r.cfg["url"]))),
        ]
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</table>")

    # totals
    out.append('<p style="margin:10px 0 4px">')
    out.append(f"<b>Current total: {fmt_eur(rep.total)}</b> vs base total {fmt_eur(rep.base_total)}: "
               f"{styled_span(fmt_eur_signed(rep.total_diff), rep.total_diff)}")
    if rep.total_parts:
        out.append(" — " + html.escape(", ".join(rep.total_parts)))
    out.append("</p>")
    if rep.alt_label:
        if rep.alt_total is not None:
            out.append(f'<p style="margin:4px 0">Total {html.escape(rep.alt_label)}: <b>{fmt_eur(rep.alt_total)}</b> '
                       f"({styled_span(fmt_eur_signed(rep.alt_diff), rep.alt_diff)} vs base)"
                       + (" — " + html.escape(", ".join(rep.alt_parts)) if rep.alt_parts else "") + "</p>")
        elif rep.alt_note:
            out.append(f'<p style="margin:4px 0">Total {html.escape(rep.alt_label)}: {html.escape(rep.alt_note)}</p>')
    if rep.purchases_line:
        out.append(f'<p style="margin:4px 0">{html.escape(rep.purchases_line)}</p>')
    out.append('<p style="margin:8px 0 2px"><b>Price drops below base:</b></p>')
    if rep.drops:
        out.append('<ul style="margin:2px 0 8px">')
        for r in rep.drops:
            ns = "" if r.res.in_stock is not False else " – not in stock"
            out.append(f"<li>{html.escape(r.cfg['id'] + ' ' + r.cfg['item'])}: {fmt_eur(r.res.price)} "
                       f"({styled_span(fmt_signed(r.diff), r.diff)} vs base {fmt_eur(r.base)}){html.escape(ns)}</li>")
        out.append("</ul>")
    else:
        out.append('<p style="margin:2px 0 8px">none</p>')

    # filter table
    out.append(f'<h3 style="font-size:16px;margin:16px 0 6px">{html.escape(FILTER_TITLE)}</h3>')
    out.append(f'<table style="{TABLE}"><tr>' + "".join(f'<th style="{TH}">{html.escape(c)}</th>' for c in FILTER_COLUMNS) + "</tr>")
    for f in rep.filter_rows:
        res = f.res
        links = _a(f.cfg["url"], "results")
        if res.product_url:
            links += " · " + _a(res.product_url, "product")
        base = fmt_eur(f.base) + (f" ({f.cfg['base_note']})" if f.cfg.get("base_note") else "")
        product = _a(res.product_url, res.product) if res.product else DASH
        note = ""
        if res.notes:
            note = f'<br><span style="color:#666;font-size:11px">{html.escape("; ".join(res.notes))}</span>'
        cells = [
            _td(html.escape(f"{f.cfg['id']} {f.cfg['label']}")),
            _td(html.escape(_price_cell(res)) + note, "white-space:nowrap"),
            _td(product),
            _td(html.escape(res.shop or DASH) if res.ok else DASH),
            _td(html.escape(_stock_cell(res))),
            _td(html.escape(base)),
            _td(fmt_signed(f.diff), (diff_style(f.diff) + ";white-space:nowrap").lstrip(";")),
            _td(links),
        ]
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</table>")
    out.append('<p style="margin:8px 0 2px"><b>Drops below base in this block:</b> ')
    if rep.filter_drops:
        out.append(", ".join(f"{html.escape(f.cfg['id'])} {fmt_eur(f.res.price)} ({styled_span(fmt_signed(f.diff), f.diff)})"
                             + (" – not in stock" if f.res.in_stock is False else "") for f in rep.filter_drops))
    else:
        out.append("none")
    out.append("</p>")
    out.append(f'<p style="margin:4px 0;font-size:12px;color:#444">{html.escape(rep.skipped_line)}</p>')
    out.append(f'<p style="margin:4px 0">{_compare_html(rep)}</p>')

    if screenshots:
        out.append('<h3 style="font-size:16px;margin:16px 0 6px">Screenshots</h3>')
        for title, rel in screenshots:
            out.append(f'<p style="margin:8px 0 2px">{html.escape(title)}</p>'
                       f'<img src="{html.escape(rel, quote=True)}" style="max-width:100%;border:1px solid #ccc">')
    out.append("</div>")
    return "\n".join(out)


def _compare_html(rep: Report) -> str:
    parts = []
    if rep.compare_ram:
        d, price, fid, pat = rep.compare_ram
        parts.append(f"Cheapest spec RAM ({html.escape(fid)} {fmt_eur(price)}) vs Patriot ({html.escape(pat)}): "
                     f"{styled_span(fmt_signed(d), d)}")
    else:
        parts.append("Cheapest spec RAM vs Patriot: n/a")
    if rep.compare_gpu:
        d, f3, i2 = rep.compare_gpu
        parts.append(f"F3 ({fmt_eur(f3)}) vs item 2 ({fmt_eur(i2)}): {styled_span(fmt_signed(d), d)}")
    else:
        parts.append("F3 vs item 2: n/a")
    return "; ".join(parts)


def wrap_page(body: str, title: str) -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title></head>'
            f'<body style="margin:16px;background:#fff">{body}</body></html>')


# ----------------------------------------------------------------------------- plain text
def render_text(rep: Report) -> str:
    L = []
    if rep.failed:
        L.append(_failed_line(rep.failed))
        L.append("")
    L.append(f"White Build prices – {rep.now:%Y-%m-%d %H:%M} {rep.tz_label}")
    L.append("")
    for r in rep.rows:
        res = r.res
        bought, saved, pct = _bought_cells(r)
        line = (f"{r.cfg['id']:>3} {r.cfg['item']:<12} {_price_cell(res):<28} diff {fmt_signed(r.diff):>9} "
                f"(base {fmt_eur(r.base)})")
        if res.ok:
            line += f" | {res.shop or DASH} | {_stock_cell(res)}"
        if r.purchase:
            line += f" | bought {bought}, saved {saved} ({pct})"
        L.append(line)
        L.append(f"      {r.cfg['url']}")
    L.append("")
    L.append(f"Current total: {fmt_eur(rep.total)} vs base total {fmt_eur(rep.base_total)}: "
             f"{fmt_eur_signed(rep.total_diff)}" + (" — " + ", ".join(rep.total_parts) if rep.total_parts else ""))
    if rep.alt_label:
        if rep.alt_total is not None:
            L.append(f"Total {rep.alt_label}: {fmt_eur(rep.alt_total)} ({fmt_eur_signed(rep.alt_diff)} vs base)"
                     + (" — " + ", ".join(rep.alt_parts) if rep.alt_parts else ""))
        elif rep.alt_note:
            L.append(f"Total {rep.alt_label}: {rep.alt_note}")
    if rep.purchases_line:
        L.append(rep.purchases_line)
    L.append("Price drops below base: " + ("none" if not rep.drops else ""))
    for r in rep.drops:
        ns = " – not in stock" if r.res.in_stock is False else ""
        L.append(f"  - {r.cfg['id']} {r.cfg['item']}: {fmt_eur(r.res.price)} ({fmt_signed(r.diff)}){ns}")
    L.append("")
    L.append(FILTER_TITLE)
    for f in rep.filter_rows:
        res = f.res
        line = f"{f.cfg['id']:>3} {_price_cell(res):<28} diff {fmt_signed(f.diff):>9} (base {fmt_eur(f.base)})"
        if res.product:
            line += f" | {res.product}"
        if res.ok:
            line += f" | {res.shop or DASH} | {_stock_cell(res)}"
        L.append(line)
        if res.product_url:
            L.append(f"      {res.product_url}")
        if res.notes:
            L.append(f"      note: {'; '.join(res.notes)}")
    L.append("Drops below base in this block: " + (", ".join(
        f"{f.cfg['id']} {fmt_eur(f.res.price)} ({fmt_signed(f.diff)})" for f in rep.filter_drops) or "none"))
    L.append(rep.skipped_line)
    cmp = []
    if rep.compare_ram:
        d, price, fid, pat = rep.compare_ram
        cmp.append(f"Cheapest spec RAM ({fid} {fmt_eur(price)}) vs Patriot ({pat}): {fmt_signed(d)}")
    else:
        cmp.append("Cheapest spec RAM vs Patriot: n/a")
    if rep.compare_gpu:
        d, f3, i2 = rep.compare_gpu
        cmp.append(f"F3 ({fmt_eur(f3)}) vs item 2 ({fmt_eur(i2)}): {fmt_signed(d)}")
    else:
        cmp.append("F3 vs item 2: n/a")
    L.append("; ".join(cmp))
    return "\n".join(L) + "\n"


def save(rep: Report, run_dir: Path, results: dict[str, RowResult], screenshots: list[tuple[str, str]]) -> None:
    import json

    run_dir.mkdir(parents=True, exist_ok=True)
    body = render_html(rep, screenshots)
    (run_dir / "report.html").write_text(wrap_page(body, rep.subject), encoding="utf-8")
    (run_dir / "report.txt").write_text(rep.subject + "\n\n" + render_text(rep), encoding="utf-8")
    data = {
        "subject": rep.subject,
        "generated": rep.now.isoformat(),
        "total": str(rep.total),
        "total_diff": str(rep.total_diff),
        "alt_total": str(rep.alt_total) if rep.alt_total is not None else None,
        "results": {k: v.to_json() for k, v in results.items()},
    }
    (run_dir / "results.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
