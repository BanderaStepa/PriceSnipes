# White Build price checker: build spec

This file is the complete brief for Claude Code. Put it in the root of the GitHub repo and tell the cloud session: **"Build everything described in BUILD_SPEC.md."**

---

## 0. Goal and hard rules

Build a Python program that runs on a **Windows 11 PC** every 4 hours (Windows Task Scheduler). It checks the prices of my PC build ("White Build") on geizhals.de, idealo.de and amazon.de, builds a report and emails it to me. **No AI or Claude is used at runtime**: it is a plain script.

Hard rules:
1. **Never guess a price.** If a page fails to load or no price can be read, the row says so (`page failed to load` / `no price found`) and the report is still sent.
2. **No bot-detection evasion.** No stealth plugins, no fingerprint spoofing, no CAPTCHA solving, no proxies. If a site shows a CAPTCHA or "robot check", mark that row `blocked by site (captcha)` and move on.
3. Be polite: load pages **one at a time**, wait 3–6 s (random) between pages, and use one normal browser context. Every 4 h is the maximum frequency.
4. Secrets (Gmail address and app password) come from environment variables or a local `.env` file that is in `.gitignore`. Never commit them.
5. Fixed base prices never change. Always compare against the bases in `config.json`, never against the previous run.

Important for the builder: **the cloud session cannot reach these shops.** Build against the page descriptions below and unit-test with synthetic HTML fixtures. The first live test happens on my PC, so make debugging easy (see §9).

---

## 1. Tech stack

- Python 3.12, **Playwright for Python** (Chromium, headless by default; `--headed` flag to watch it), `tzdata` (needed for `zoneinfo` on Windows), `python-dotenv`, `pytest`.
- Email: `smtplib` + `email.message` over Gmail SMTP (`smtp.gmail.com:465`, SSL) with a Google **app password**.
- Browser: `chromium.launch_persistent_context("./browser-profile", locale="de-DE", timezone_id="Europe/Berlin", viewport 1400x900)`. The persistent profile keeps cookie-banner choices between runs. Add `browser-profile/` to `.gitignore`.

Repo layout (suggested):
```
BUILD_SPEC.md
README.md              # Windows setup, step by step, for a non-programmer
CLAUDE.md              # short notes for future Claude Code sessions (see §11)
config.json            # items, links, bases, purchases, filters
pricecheck.py          # entry point
pricecheck/            # sites/geizhals.py, sites/idealo.py, sites/amazon.py, consent.py,
                       # parse.py, specs.py, report.py, emailer.py
tests/                 # pytest + fixtures/*.html
requirements.txt
run_pricecheck.bat     # activates venv, runs script, appends to logs\run.log
setup_task.ps1         # creates the scheduled task (every 4 h)
.env.example           # GMAIL_ADDRESS=, GMAIL_APP_PASSWORD=, MAIL_TO=
.gitignore             # .env, browser-profile/, runs/, logs/, .venv/
```

CLI:
- `python pricecheck.py`: full run plus email.
- `--no-email`: run and save the report only.
- `--only 2,6c,F3`: check only these rows (for debugging).
- `--headed`: show the browser.
- `--test-email`: send a tiny test mail and exit.

---

## 2. config.json (put this data in exactly)

```json
{
  "mail_to": "gaidukigors@gmail.com",
  "timezone": "Europe/Kyiv",
  "base_total": 2848.72,
  "items": [
    {"id":"1","item":"CPU","name":"AMD Ryzen 7 7800X3D TRAY","base":271.05,"site":"geizhals",
     "url":"https://geizhals.de/amd-ryzen-7-7800x3d-100-100000910-a2872867.html"},
    {"id":"2","item":"GPU","name":"GIGABYTE RTX 5080 Aero OC SFF 16G","base":1569.00,"site":"geizhals",
     "url":"https://geizhals.de/gigabyte-geforce-rtx-5080-aero-oc-sff-16g-gv-n5080aero-oc-16gd-a3381783.html?utm_source=gemini"},
    {"id":"3","item":"Motherboard","name":"MSI B850 Gaming Plus WIFI6E","base":155.84,"site":"geizhals",
     "url":"https://geizhals.de/msi-b850-gaming-plus-wifi6e-7e80-001r-a3525842.html?utm_source=gemini"},
    {"id":"4","item":"SSD","name":"TeamGroup MP44L 500GB (TM8FPK500G0C101)","base":114.90,"site":"geizhals",
     "url":"https://geizhals.de/teamgroup-mp44l-500gb-tm8fpk500g0c101-a2791657.html?utm_source=gemini"},
    {"id":"5","item":"PSU","name":"Thermalright TG-850S-W 850W","base":76.38,"site":"geizhals",
     "url":"https://geizhals.de/thermalright-tg-850s-w-850w-atx-3-0-tr-tg-850s-w-a3144270.html"},
    {"id":"6a","item":"RAM","group":"RAM_PATRIOT","name":"Patriot Viper Xtreme 5 RGB MPOWER 32GB DDR5-6000 CL30 (idealo)","base":519.90,"site":"idealo",
     "url":"https://www.idealo.de/preisvergleich/OffersOfProduct/204432983_-viper-xtreme-5-rgb-mpower-32gb-kit-ddr5-6000-cl30-pvxr532g60c30km-patriot.html?utm_source=gemini"},
    {"id":"6b","item":"RAM","group":"RAM_PATRIOT","name":"Patriot Viper Xtreme 5 RGB MPOWER 32GB DDR5-6000 CL30 (geizhals)","base":519.90,"site":"geizhals",
     "url":"https://geizhals.de/patriot-viper-xtreme-5-rgb-mpower-dimm-kit-32gb-pvxr532g60c30km-a3218114.html"},
    {"id":"6c","item":"RAM alt","group":"RAM_TFORCE","name":"TeamGroup T-Force Delta RGB white 32GB DDR5-6000 CL30 (idealo)","base":479.00,"site":"idealo",
     "url":"https://www.idealo.de/preisvergleich/OffersOfProduct/202204151_-t-force-delta-rgb-32gb-kit-ddr5-6000-cl30-ff4d532g6000hc30dc01-team-group.html"},
    {"id":"6d","item":"RAM alt","group":"RAM_TFORCE","name":"TeamGroup T-Force Delta RGB white 32GB DDR5-6000 CL30 (geizhals)","base":479.00,"site":"geizhals",
     "url":"https://geizhals.de/teamgroup-t-force-delta-rgb-weiss-dimm-kit-32gb-ff4d532g6000hc30dc01-a2748939.html"},
    {"id":"7a","item":"Cooler","group":"COOLER","name":"ARCTIC Liquid Freezer III Pro 240 A-RGB white (Amazon, renewed)","base":50.51,"site":"amazon",
     "url":"https://www.amazon.de/dp/B0GPCDJ7W5/?smid=AUBM4K0YLFI9J&tag=preisvergleich-idealode-21&linkCode=asn&creative=6742&camp=1638&creativeASIN=B0GPCDJ7W5&ascsubtag=2026-09-21_9bd703fbc3abd0e9a70a7dc74d566bbac1b916e57de4f8494cb3f283914ffeb3&th=1"},
    {"id":"7b","item":"Cooler","group":"COOLER","name":"ARCTIC Liquid Freezer III Pro 240 A-RGB white (new, ACFRE00186A)","base":68.32,"site":"geizhals",
     "url":"https://geizhals.de/arctic-liquid-freezer-iii-pro-240-a-rgb-acfre00186a-a3573851.html"},
    {"id":"8a","item":"Case","group":"CASE","name":"Jonsbo TK-3 White (idealo)","base":91.14,"site":"idealo",
     "url":"https://www.idealo.de/preisvergleich/OffersOfProduct/204503911_-tk-3-weiss-jonsbo.html"},
    {"id":"8b","item":"Case","group":"CASE","name":"Jonsbo TK-3 White (geizhals)","base":91.14,"site":"geizhals",
     "url":"https://geizhals.de/jonsbo-tk-3-white-a3222506.html"}
  ],
  "build_total_groups": ["1","2","3","4","5","RAM_PATRIOT","COOLER","CASE"],
  "alt_total_swap": {"from":"RAM_PATRIOT","to":"RAM_TFORCE","label":"with T-Force RAM instead of Patriot"},
  "purchases": [],
  "filters": [
    {"id":"F1","kind":"ram","site":"geizhals","base":429.00,"base_note":"was Lexar ARES OC 32GB Kit",
     "label":"RAM 32GB Kit DDR5-6000 CL30, any brand (geizhals)",
     "url":"https://geizhals.de/?fs=32GB+Kit%2C+DDR5-6000%2C+CL30&hloc=at&hloc=de&sort=p"},
    {"id":"F2","kind":"ram","site":"idealo","base":429.00,"base_note":"was Lexar ARES OC 32GB Kit",
     "label":"RAM 32GB Kit DDR5-6000 CL30, any brand (idealo)",
     "url":"https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q=32GB%20Kit%20DDR5-6000%20CL%2030&sortKey=minPrice"},
    {"id":"F3","kind":"gpu","site":"geizhals","base":1457.90,"base_note":"was Palit GeForce RTX 5080 Infinity 3",
     "label":"GPU RTX 5080, any brand (geizhals, €707–6,999)",
     "url":"https://geizhals.de/?fs=RTX+5080&sort=p&hloc=at&hloc=de&bpmin=707&bpmax=6999"}
  ]
}
```

Purchase entry format, for when I buy something:
```json
{"item_group":"2","price":1540.00,"shop":"Mindfactory","date":"2026-10-02"}
```
`item_group` is an item id (`"1"`–`"5"`) or a group name (`"RAM_PATRIOT"`, `"RAM_TFORCE"`, `"COOLER"`, `"CASE"`).

---

## 3. Generic page handling

- `page.goto(url, wait_until="domcontentloaded", timeout=45000)`, then wait up to 10 s for the offer list or result list.
- On error, retry once after 10 s. Then give up: `page failed to load`.
- **CAPTCHA detection:** if the page text contains `captcha`, `Geben Sie die Zeichen unten ein`, `Robot Check` or `sind Sie ein Mensch`, the row is `blocked by site (captcha)`. Do not interact with the check.
- **Cookie banners (`consent.py`):** after each navigation, try (up to about 3 s) to click a button whose accessible name matches, case-insensitive:
  `Alle ablehnen|Ablehnen|Nur notwendige|Nur erforderliche|Nur essenzielle|Weiter ohne Einwilligung|Reject all|Decline|Necessary only`.
  - Search the main page **and all iframes** (idealo's consent may be in an iframe). Playwright role locators pierce open shadow DOM, which covers Usercentrics-style banners.
  - Only if no reject/necessary option exists, close the banner (X / `Schließen`).
  - Never click "Accept all".
  - Thanks to the persistent profile, this usually only happens on the first run.
- **Price parsing (`parse.py`):** handle German `€ 1.556,40`, `1.556,40 €`, `€ 76,38` and English `€1,556.40`, `€271.54`.
  - Chrome's translate may be active in a headed run, so support both formats.
  - Ignore per-unit prices like `(€ 13,91 / GB)`.
  - Return a `Decimal`, rounded to 2 places.
- **Stock classification (`parse.py`):** return `(in_stock: bool, text)`. Always show the shop's own short availability text in the report (for example `Yes (Auf Lager, 1-2 Werktage)` or `No (4-10 Arbeitstage)`).
  - **In stock** if the text matches: `auf lager`, `lagernd` (but not `nicht lagernd`), `sofort lieferbar`, `sofort verfügbar`, `ab lager lieferbar`, `in stock`, `available from stock`, `nur noch \d+ auf lager`.
  - **Not in stock** if the text matches: `nicht lagernd`, `bestellt`, `wird .* erwartet`, `nicht verfügbar`, `derzeit nicht`, or only a delivery-time range like `4-10 Arbeitstage`, `Lieferzeit 5-7 Werktage`, `7-13 Werktagen`.
  - Special cases: `Lieferzeit 1-2 Werktage` without a stock word counts as in stock. On idealo, "delivery by <date>" with a green dot counts as in stock; a yellow or orange dot means not in stock.

---

## 4. geizhals product pages (items 1–5, 6b, 6d, 7b, 8b)

What the page looks like (checked live on 2026-09-29):
- The offer list sits below the product header and specs, preceded by a filter bar ("inkl. Versand", DE/AT flags, "lagernd beim Händler") and the header row `Preis exkl. Versand* | Anbieter | Händlerbewertung | Verfügbarkeit / Versandkosten* | Produktbezeichnung des Händlers`.
- **It is already sorted by price, lowest first.**
- Offer rows match the CSS `#offer__list .offer` (fallback: `.offerlist .offer`, `[id^="offer__"]`).
- A row's `innerText` looks like this example: `€ 1556,40 zum Angebot MobPicker Hinweis: Firmensitz in Polen Infos AGB ... 4-10 Arbeitstage Vorkasse, Nachnahme, Kreditkarte GRATISVERSAND ...`.
  - Other seen rows: `€ 153,99 zum Angebot x-kom ... sofort lieferbar, Lieferzeit 1-3 Werktage ...` and `€ 111,07 zum Angebot Mindfactory ... Bestellt, wird in 2 Werktagen erwartet ...`.
  - Sometimes there is a label between the price and the shop: `€ 92,82 GUTSCHEINAKTION zum Angebot voelkner.de`.
- Shop name: the text right after `zum Angebot` (strip `GUTSCHEINAKTION`), or the merchant-name/logo alt element inside the row. Also include a marketplace note if present, e.g. `GadgetLifestyle (via Amazon Marketplace)`.
- Availability: the text of the availability cell. The first phrase before the payment methods is enough.
- Take the **minimum price over all parsed rows**. It will normally be the first row.
- Screenshot: `row.scroll_into_view_if_needed()`, then an element screenshot of that row (plus the header row if easy) → `runs/<stamp>/<id>.png`.
- Product title for logging: `document.title` (e.g. `MSI B850 Gaming Plus WIFI6E ab € 153,99 (2026) | Preisvergleich Geizhals Deutschland`). The "ab € X" in the title is a good sanity check that the lowest row was found.

---

## 5. idealo product pages (6a, 6c, 8a)

What the page looks like (checked live):
- Below the product header there is a **"Preisvergleich"** box with the checkboxes "Sofort lieferbar" and "Ohne Rücksendekosten" and a "Sortieren nach: Preis | Gesamtpreis" toggle. **"Preis" is selected by default, so the list is sorted lowest first.**
- Each offer is a row with these columns: `Angebotsbezeichnung` (offer title link), `Preis & Versand` (big price like `519,90 €`, sometimes `Günstigster Gesamtpreis`, and `519,90 € inkl. Versand`), `Zahlungsarten`, `Lieferung` (like `Auf Lager`, `Lieferung: bis Mi. 30.09.` with a green dot, or `bis Mo. 12.10.` with a yellow dot), `Shop & Shopbewertung` (logo image with alt text, e.g. `amazon.de marketplace` + `Verkauf durch: GadgetLifestyle`, `GALAXUS`, `ALTERNATE`, `voelkner`), and a green **"Zum Shop"** button.
- Selectors are unknown and idealo changes class names, so **locate rows structurally**: find each "Zum Shop" button/link inside the Preisvergleich container and take its nearest ancestor that also contains a `€` price. Log a warning if 0 rows are found.
- Price: the big price, not the "inkl. Versand" one. Use the minimum over rows.
- Shop: the logo `alt`/`title` text, plus `Verkauf durch: X` if present.
- **Coupon/membership prices:** if the row contains `Preis nur mit`, `voelkner+` or `Preis inkl. Gutschein`, keep it as the lowest listed price but add the note `(price only with <coupon/membership>)` to the Lowest price cell. Example seen: `Preis nur mit voelkner+ | Jonsbo TK-3 ...` at `92,82 €`.
- Stock: from the `Lieferung` cell. Use a green vs yellow dot (a CSS class or computed colour of the bullet) if you can detect it. Otherwise use the text rules from §3, where `Auf Lager` means in stock.
- Also make sure the **"Neu"** offers tab is selected, not "B-Ware & Gebraucht". It is the default.
- Screenshot the cheapest row.

---

## 6. Amazon (7a): renewed, white variant

What the page looks like (checked live):
- Title: `ARCTIC Liquid Freezer III Pro 240 A-RGB - Wasserkühlung PC(Generalüberholt)`, with a `Renewed` badge.
- Price on the listing: `-32 % 50,51 €` (old price `Neupreis: 73,99 €`).
- Buy box: `Instandgesetzt - hervorragend`, `50,51 €`, `Nur noch 1 auf Lager`, `Versender / Verkäufer: ARCTIC GmbH`.
- `Farbe: weiß` is selected. The colour swatches show `50,51€` for white and `48,18€` for black. **Use the white one, which the link selects via `th=1`.** Check that the page shows `Farbe: weiß`; otherwise the row is `wrong variant loaded`.
- Price selectors to try in order: `#corePrice_feature_div .a-offscreen`, `#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen`, `#apex_desktop .a-offscreen`, then the buy box. Do not use the sponsored ad banner at the top (it shows a different product at 73,99).
- Stock: `#availability` text (`Nur noch 1 auf Lager` counts as in stock). If you see `Derzeit nicht verfügbar`, it is not in stock.
- Shop: the seller from the buy box (`#merchantInfoFeature_feature_div` / `#sellerProfileTriggerId`); show it as `Amazon (seller ARCTIC GmbH)`.
- Handle the Amazon cookie dialog (`Ablehnen`). If Amazon asks for a location or shows a CAPTCHA, see §3.
- Screenshot: the price block plus the buy box, or the visible viewport.

---

## 7. Spec filter pages (F1, F2, F3): cheapest part with the required specs, any brand

These links open **search results sorted by price, lowest first**. Walk down the results, take the **first product that really matches the specs**, open it, and read its lowest offer as in §4 or §5. Record every skipped hit (name plus reason) for the report line.

**geizhals search results (F1, F3):**
- Product cards/rows match `.listview__item`, `.productlist__product` or `article`.
- The text of a real product looks like: `goodram IRDM BLACK SILVER UDIMM 32GB Kit, DDR5-6000, CL30-36-36-76, 1RX8 IR-6000D564L30S/32GDC ... 8 Angebote ab € 445,26`.
- **Variant groups** start with `VARIANTEN` (e.g. `VARIANTEN ASUS GeForce RTX 5070 Ti ... ab € 1249,00`) and have no single product link. Skip them, reason `variant group`. Real products link to `https://geizhals.de/<slug>-a<digits>.html`.
- Price on a card: `ab € 445,26` or `um € 464,36`.
- Card order can differ slightly from strict price order. So collect all cards on page 1, sort them by their card price, then apply the spec rules in that order.

**idealo search results (F2):**
- Product tiles have a name, a spec line and `NN Angebote ab 479,00 €` or `ab479,00 €`. Example spec line: `DDR5-RAM, 32 GB, Anzahl Module 2, Kapazität pro Modul 16 GB, 6.000 MT/s, PC5-48.000, CL 30-40-40-76, 1,35 V, UDIMM, ...`.
- Some tiles are single marketplace offers (`Verkauf durch: Amazon Marketplace ... 499,99 € inkl. MwSt.`). They count if the specs match.
- Tiles marked `gebraucht` count as used: skip them.
- To open a product, click its name link. The product page is then read as in §5.

**Spec rules for RAM (F1, F2).** Match against the name plus the spec line, case-insensitive. All of these must be true:
- 32 GB total: `32\s?GB` present and no `64\s?GB`/`16GB Kit`/`48GB`.
- Two modules: `Kit`, `2x16`, `2 x 16` or `Anzahl Module 2`. Skip anything with `Anzahl Module 1` or a single 32GB module like `KF560C30BBE-32` (reason `single module`).
- DDR5-6000: `DDR5-6000`, `6000 ?MT/s`, `6.000 MT/s` or `6000MHz`.
- CL30: `CL\s?30` (`CL30-36-36-76`, `CL 30-40-40`, `Latenz CL30`). CL28, CL32, CL36, CL38, CL48 and so on do not count.
- Not SO-DIMM: no `SODIMM`/`SO-DIMM`/`Notebook`/`Laptop`.
- Not a complete PC: no `Gaming-PC`, `PC-System`, `Komplett`, `Systeme`, `Ryzen`/`Core i`/`GeForce` in the name.
- Not used/B-Ware.
- The tile must actually state the specs. A vague tile like `GoodRAM IRDM RGB DDR5` with no capacity/speed/CL in name or spec line is skipped (reason `specs not listed`).

**Spec rules for the GPU (F3):**
- `RTX\s?5080` present.
- Not `5070`, `5060`, `5090`, `5080 Ti`, `5080 SUPER`.
- Not a notebook/laptop/complete system (`Notebook`, `Laptop`, `Gaming-PC`, `Komplettsystem`, `Systeme`).
- Not a variant group.
- 16GB is expected (`16GB`/`16G`). If the card text doesn't mention memory, allow it.

**Screenshots for filters:** the search-result card of the chosen product, plus the lowest offer row on its product page.

**Unit tests (required).** Build a test with these real names from 2026-09-29 and their expected decisions.
- F1/F2, should match:
  - `goodram IRDM BLACK SILVER UDIMM 32GB Kit, DDR5-6000, CL30-36-36-76, 1RX8`
  - `Patriot Viper Venom RGB 32GB Kit DDR5-6000 CL30 (PVVR532G600C30K)`
  - `Team T-Force DELTA RGB 32GB Kit DDR5-6000 CL30 (FF4D532G6000HC30DC01)`
- F1/F2, should skip:
  - `Kingston 32GB DDR5-5600 CL46 (KCP556SD8-32)` + `SODIMM` (speed/SO-DIMM)
  - `Crucial Pro Overclocking 32GB Kit DDR5-6000 CL36` (CL36)
  - `XPG Lancer Blade 32GB Kit DDR5-6000 CL48` (CL48)
  - `GoodRAM IRDM RGB DDR5` (specs not listed)
  - `Kingston FURY Impact 32GB Kit DDR5-6000 CL38 ... SODIMM` (SO-DIMM/CL38)
  - `Patriot Viper Venom RGB 32GB Kit DDR5-6000 CL28` (CL28)
  - `Kingston FURY Beast 32GB DDR5-6000 CL30 (KF560C30BWE-32)` + `Anzahl Module 1` (single module)
- F3, should match:
  - `Gainward GeForce RTX 5080 Phoenix, 16GB GDDR7, HDMI, 3x DP`
  - `Palit GeForce RTX 5080 Infinity 3, 16GB GDDR7`
- F3, should skip:
  - `VARIANTEN ASUS GeForce RTX 5070 Ti` (variant group / 5070 Ti)
  - `MSI GeForce RTX 5070 Ti` (5070 Ti)

---

## 8. Report (identical logic to my manual routine)

All amounts are in € with 2 decimals, formatted like `€1,556.40` (English style). Differences are signed: `+0.49`, `−12.60`, `0.00`.

**Main table.** Columns: `Item | Lowest price | Shop | In stock | Base | Difference in € (negative = cheaper) | Bought at | Saved € | Saved % | Link`.
- **13 rows, always, in this order:** 1, 2, 3, 4, 5, 6a, 6b, 6c, 6d, 7a, 7b, 8a, 8b. Include rows even when the price is unchanged or the read failed.
- Difference = current − row's base. Rows 6c/6d use base 479.00 and 7b uses 68.32 (each row has its own base in the config).
- Bought at / Saved € / Saved %: from `purchases`, filled on **all rows of the bought item/group**.
  - Saved € = row base − bought price. Negative means I paid more than base.
  - Saved % = Saved € / row base × 100, with 1 decimal.
  - Write `—` when not bought.
- Bought items are still checked as usual.
- Failed rows: Lowest price = `page failed to load` / `no price found` / `blocked by site (captcha)`, and the other price cells are `—`.

**Below the table:**
1. **Current total** vs base total 2,848.72, with the € difference.
   - Built from groups `1,2,3,4,5,RAM_PATRIOT,COOLER,CASE`. For each group use the **lower** current price of its links (RAM = lower of 6a/6b, Cooler = lower of 7a/7b, Case = lower of 8a/8b) and **say which link was used** (e.g. `RAM = 6a/6b (tie €519.90), Cooler = 7a (€50.51), Case = 8b (€91.13)`).
   - For a **bought** item or group, use the bought price instead.
   - If a group has no readable price, use its base and say `(no current price, base used)`.
2. One line with the total if T-Force (lower of 6c/6d) is used instead of Patriot.
3. If anything is bought: one line with the total spent so far, the total saved in € and % against the bases of the bought items, and what is still left to buy.
4. **Price drops below base:** a list of rows with a negative difference, noting "not in stock" where relevant.

**Spec filter block.** Title: `Spec filter links (cheapest part with the required specs, any brand)`.
- Table columns: `Filter | Lowest price | Product | Shop | In stock | Base | Difference in € (negative = cheaper) | Link`, with 3 rows: F1, F2, F3.
- Below the table:
  - the drops below base in this block;
  - one line listing the skipped non-matching hits (grouped by reason, short);
  - one line comparing the cheapest spec RAM (lower of F1/F2) with the current Patriot price (lower of 6a/6b), and F3 with item 2, in €.
- If no matching product is found on the results page, say so in that row.

**Colours (email and HTML report).** In both tables, colour every "Difference in €" cell, and also the € differences in the total lines and the drop lists:
- negative: `background:#d4edda;color:#155724;font-weight:bold`
- positive: `background:#f8d7da;color:#721c24;font-weight:bold`
- `0.00`: no highlight

Use inline styles only (Gmail strips `<style>`).

**Email:**
- To `mail_to` only.
- Subject: `White Build prices – <YYYY-MM-DD> <HH:MM Kyiv> – total €<current total> (<+/-€ vs base>)`, e.g. `White Build prices – 2026-09-29 12:15 Kyiv – total €2,830.92 (−€17.80 vs base)`.
- Body: both HTML tables with clickable links, the total lines, the drop lists and the filter notes, plus a plain-text alternative.
- **Attach all screenshots** as PNG/JPEG (named `01_CPU.png`, `06c_TForce_idealo.png`, `F3_offer.png`, …). If the total size exceeds 20 MB, downscale them.
- If a run has failed rows, add a first line in red: `⚠ N rows could not be read: 6a (page failed to load), …`.

**Saved locally every run:** `runs/<YYYY-MM-DD_HHMM>/report.html`, `results.json` (all raw values) and the screenshots. Keep the last 60 runs and delete older ones.

**If sending fails:** log the error in `logs/run.log`, keep the report on disk, exit code 2.

---

## 9. Debugging support (important: first live run is on my PC)

- If a row fails to parse, save `runs/<stamp>/debug/<id>.html` (page HTML) and `<id>_full.png` (full-page screenshot).
- Logging with timestamps to `logs/run.log` and the console: each URL, the parsed rows (price/shop/stock) and the decision.
- In the README, describe how to fix breakage: "Commit the `debug/` folder of the failing run to the repo and ask a Claude Code cloud session: *Row X failed, the page HTML is in runs/…/debug/, fix the extractor.*"
- Design the extractors so that each site's selectors and phrases sit at the top of its module as constants.

---

## 10. Windows setup (put this in README.md, written for a non-programmer)

1. Install **Python 3.12** from python.org and tick "Add python.exe to PATH". Install **Git for Windows**.
2. `git clone <repo-url>` into e.g. `C:\PriceCheck`, then open PowerShell in that folder.
3. Run:
   ```
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   python -m playwright install chromium
   ```
4. Gmail app password: my Google account → Security → 2-Step Verification must be on → "App passwords" → create one called "PriceCheck". Copy `.env.example` to `.env` and fill in `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` and `MAIL_TO`.
5. Test:
   - `python pricecheck.py --test-email`
   - then `python pricecheck.py --no-email --headed` (watch it)
   - then `python pricecheck.py`
6. Schedule: `powershell -ExecutionPolicy Bypass -File setup_task.ps1`. This creates the task "White Build price check".
   - It runs `run_pricecheck.bat` every 4 hours, starting at the next full hour.
   - Settings: "Run only when user is logged on" (simplest), "Start the task as soon as possible after a scheduled start is missed" = on, "Wake the computer to run this task" = on, stop the task if it runs longer than 30 min.
   - Explain how to check it in Task Scheduler and how to delete it.
7. Explain how to add a purchase or change links: edit `config.json` (with an example).

`setup_task.ps1` should use `Register-ScheduledTask` with `New-ScheduledTaskTrigger -Once -At <next hour> -RepetitionInterval (New-TimeSpan -Hours 4)` and settings for StartWhenAvailable, WakeToRun and ExecutionTimeLimit.

---

## 11. CLAUDE.md (for future Claude Code sessions)

Short notes:
- This repo is a no-AI price checker. Keep it deterministic.
- Never add bot-evasion or CAPTCHA solving.
- Bases never change.
- When the user says "I bought X for €Y at Z", add an entry to `config.json` → `purchases` with today's date and change nothing else.
- Run `pytest` after every change.

---

## 12. Acceptance checks (the builder must run these as pytest)

1. Price parsing: `€ 1556,40` → 1556.40; `€ 1.556,40` → 1556.40; `1.556,40 €` → 1556.40; `€271.54` → 271.54; `519,90 € inkl. Versand` → 519.90; `(€ 13,91 / GB)` is ignored.
2. Stock rules on these strings:
   - `Auf Lager, 1-2 Werktage` → in stock
   - `4-10 Arbeitstage` → not in stock
   - `Bestellt, wird in 2 Werktagen erwartet` → not in stock
   - `Nicht lagernd, ab Bestellung verfügbar in 7-13 Werktagen` → not in stock
   - `Lagernd im Außenlager, Lieferung 2-3 Werktage` → in stock
   - `Nur noch 1 auf Lager` → in stock
3. Spec rules on the names in §7.
4. **Report math with the real results of 2026-09-29.** Current prices:
   | Row | Price |
   |---|---|
   | 1 | 271.54 |
   | 2 | 1556.40 |
   | 3 | 153.99 |
   | 4 | 111.07 |
   | 5 | 76.38 |
   | 6a | 519.90 |
   | 6b | 519.90 |
   | 6c | 486.99 |
   | 6d | 486.99 |
   | 7a | 50.51 |
   | 7b | 68.21 |
   | 8a | 92.82 |
   | 8b | 91.13 |
   | F1 | 445.26 |
   | F2 | 479.00 |
   | F3 | 1393.74 |

   Expected results:
   - differences: +0.49, −12.60, −1.85, −3.83, 0.00, 0.00, 0.00, +7.99, +7.99, 0.00, −0.11, +1.68, −0.01
   - **total 2,830.92, −17.80 vs base**, using RAM 6a/6b, Cooler 7a, Case 8b
   - T-Force total **2,798.01 (−50.71)**
   - filter differences: +16.26, +50.00, −64.16
   - comparisons: F1 vs Patriot **−74.64**, F3 vs item 2 **−162.66**
5. Purchase math: with the purchase `{"item_group":"2","price":1540.00}`, rows show Bought at €1,540.00, Saved €29.00 and Saved 1.8%, and the total uses 1540.00.
6. Email subject format as in §8.
7. Rendered HTML contains the green/red inline styles on the right cells.
