# White Build price checker

A small Python program that checks the prices of the "White Build" PC parts on
**geizhals.de**, **idealo.de** and **amazon.de** every 4 hours, builds a report and emails it
to you. It is a plain script: **no AI is used when it runs**.

What it does on each run:

- Opens every link in `config.json` one at a time in a normal Chromium browser, waiting
  3–6 seconds between pages.
- Reads the lowest offer (price, shop, stock) and takes a screenshot of it.
- Compares each price with the **fixed base prices** in `config.json` (never with the last run).
- Checks the three "any brand" spec filters (F1–F3) and picks the cheapest product that really
  matches the specs.
- Saves the report in `runs\<date_time>\` and emails it to you with the screenshots attached.

If a page doesn't load, has no readable price, or shows a CAPTCHA, that row says so
(`page failed to load` / `no price found` / `blocked by site (captcha)`), and the email is still
sent. The program never guesses a price and never tries to get around bot checks.

---

## 1. One-time setup on Windows 11

### 1.1 Install Python and Git

1. Download **Python 3.12** from <https://www.python.org/downloads/windows/> and run the installer.
   **Tick "Add python.exe to PATH"** on the first screen, then click "Install Now".
2. Download and install **Git for Windows** from <https://git-scm.com/download/win>. The default
   options are fine.

### 1.2 Download this program

1. Open **PowerShell** (Start menu → type `PowerShell` → Enter).
2. Run these commands, replacing `<repo-url>` with the address of this GitHub repository:

   ```powershell
   cd C:\
   git clone <repo-url> PriceCheck
   cd C:\PriceCheck
   ```

From now on, always run the commands in PowerShell **inside `C:\PriceCheck`**.

### 1.3 Install the program's parts

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

If PowerShell says *"running scripts is disabled on this system"* at the second line, run this once
and then repeat the second line:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

After `Activate.ps1`, the prompt starts with `(.venv)`. Do this activation step again every time you
open a new PowerShell window and want to run the program by hand.

### 1.4 Gmail app password

The program sends mail through your Gmail account using an **app password**, a separate
16-character password that only works for sending mail from programs.

1. Open <https://myaccount.google.com> → **Security**.
2. **2-Step Verification** must be **on**. Turn it on first if it's off.
3. Search for **"App passwords"** at the top of the Google Account page (or open
   <https://myaccount.google.com/apppasswords>).
4. Create one called `PriceCheck`. Google shows a 16-character password: copy it.
5. In `C:\PriceCheck`, copy `.env.example` to a new file called `.env`:

   ```powershell
   copy .env.example .env
   notepad .env
   ```

6. Fill it in and save:

   ```
   GMAIL_ADDRESS=your.address@gmail.com
   GMAIL_APP_PASSWORD=abcd efgh ijkl mnop
   MAIL_TO=gaidukigors@gmail.com
   ```

`.env` is ignored by git, so it never gets uploaded. Don't put the password anywhere else.

### 1.5 Test it

Run these one after the other:

```powershell
python pricecheck.py --test-email
```
You should get a short test email within a minute.

```powershell
python pricecheck.py --no-email --headed
```
A browser window opens and you can watch it go through all the pages (takes a few minutes).
If a cookie banner appears, the program clicks "reject" / "only necessary" by itself. The
browser remembers that choice in the `browser-profile` folder, so this normally happens only on the
first run. The report is saved in `runs\<date_time>\report.html`: double-click it to open it.

```powershell
python pricecheck.py
```
This is a full run, and it sends the email.

### 1.6 Run it automatically every 4 hours

```powershell
powershell -ExecutionPolicy Bypass -File setup_task.ps1
```

This creates the task **"White Build price check"** in Windows Task Scheduler:

- It runs `run_pricecheck.bat` **every 4 hours**, starting at the next full hour.
- "Run only when user is logged on" (the simplest option; you need to be logged in to Windows,
  though the screen may be locked).
- "Start the task as soon as possible after a scheduled start is missed" is **on**.
- "Wake the computer to run this task" is **on**.
- The task is stopped if it runs longer than 30 minutes.

A black console window appears briefly while it runs. That's normal.

**To check the task:** press Start, type `Task Scheduler`, open it, click **Task Scheduler Library**
and find **White Build price check**. The "Last Run Result" column should show `0x0` (success).
`0x2` means the email could not be sent (see `logs\run.log`). You can right-click the task and
choose **Run** to start it right away.

**To delete the task:** right-click it in Task Scheduler → **Delete**, or in PowerShell:

```powershell
Unregister-ScheduledTask -TaskName "White Build price check" -Confirm:$false
```

To change the time or interval, delete it and run `setup_task.ps1` again, or edit the task's
trigger in Task Scheduler.

---

## 2. Everyday use

### Command options

| Command | What it does |
|---|---|
| `python pricecheck.py` | Full run plus email |
| `python pricecheck.py --no-email` | Run and save the report only |
| `python pricecheck.py --only 2,6c,F3` | Check only these rows (for debugging); the others show "not checked" |
| `python pricecheck.py --headed` | Show the browser window |
| `python pricecheck.py --test-email` | Send a tiny test mail and exit |

Options can be combined, e.g. `python pricecheck.py --only 7a --headed --no-email`.

### Where things are

- `runs\<YYYY-MM-DD_HHMM>\report.html`: the report, with the screenshots at the bottom
- `runs\<YYYY-MM-DD_HHMM>\results.json`: all raw values that were read
- `runs\<YYYY-MM-DD_HHMM>\*.png`: screenshots of the cheapest offers
- `runs\<YYYY-MM-DD_HHMM>\debug\`: page HTML and full-page screenshots of rows that failed
- `logs\run.log`: log of every run (each URL, the offers read, the decision)

Only the last 60 runs are kept; older folders are deleted automatically.

Exit codes: `0` = OK, `2` = the email could not be sent (the report is still on disk),
`1` = unexpected crash (see `logs\run.log`).

### I bought something

Open `config.json` in Notepad and add an entry to `"purchases"`. `item_group` is an item id
(`"1"` to `"5"`) or a group name (`"RAM_PATRIOT"`, `"RAM_TFORCE"`, `"COOLER"`, `"CASE"`).

Before:
```json
  "purchases": [],
```
After buying the GPU (item 2) for €1,540 at Mindfactory:
```json
  "purchases": [
    {"item_group":"2","price":1540.00,"shop":"Mindfactory","date":"2026-10-02"}
  ],
```
Two purchases:
```json
  "purchases": [
    {"item_group":"2","price":1540.00,"shop":"Mindfactory","date":"2026-10-02"},
    {"item_group":"CASE","price":89.90,"shop":"Alternate","date":"2026-10-05"}
  ],
```

Mind the commas: there is a comma between entries, and none after the last one. Bought items are
still checked; the report then also shows "Bought at", "Saved €" and "Saved %", and the total uses
the price you paid.

### Changing a link

Edit the `"url"` of that item in `config.json`. **Don't change the `"base"` values**; they are the
fixed reference prices.

To check that `config.json` is still valid after editing, run
`python pricecheck.py --only 1 --no-email`. If the file is broken, the error says which line.

---

## 3. When something breaks

Shops change their pages from time to time. When that happens, a row shows
`no price found` or `page failed to load` again and again, and the email starts with a red line:
`⚠ N rows could not be read: …`.

1. Run that row alone and watch it: `python pricecheck.py --only 6a --headed --no-email`
2. Look in the newest `runs\<date_time>\debug\` folder: it holds `<row>.html` (the page as the
   program saw it) and `<row>_full.png` (a full-page screenshot).
3. If the screenshot shows a CAPTCHA or "robot check", wait a few hours. The program deliberately
   doesn't try to get past these.
4. Otherwise, commit the `debug` folder of the failing run to the repo (the `runs` folder is
   normally ignored, hence `-f`):

   ```powershell
   git add -f runs\2026-10-02_1200\debug
   git commit -m "debug files for row 6a"
   git push
   ```

   Then ask a Claude Code cloud session:
   *"Row 6a failed, the page HTML is in runs/2026-10-02_1200/debug/, fix the extractor."*

5. When the fix is pushed, update your copy with `git pull` in `C:\PriceCheck`.

Each site's selectors and phrases are at the top of its file in `pricecheck\sites\`
(`geizhals.py`, `idealo.py`, `amazon.py`), so fixes are usually small.

---

## 4. For developers

```
config.json            items, links, bases, purchases, filters
pricecheck.py          entry point
pricecheck/runner.py   one run: rows → report → files → email
pricecheck/browser.py  one polite Playwright session (persistent profile, retries, debug dumps)
pricecheck/consent.py  cookie banners: reject / necessary only, never accept
pricecheck/parse.py    price and stock parsing
pricecheck/specs.py    spec rules for F1–F3
pricecheck/sites/      geizhals.py, idealo.py, amazon.py extractors
pricecheck/report.py   report math, HTML (inline styles) and plain text
pricecheck/emailer.py  Gmail SMTP, attachments (downscaled above 20 MB)
tests/                 pytest; fixtures/*.html are synthetic pages modelled on the real ones
```

Run the tests with `pytest`. The browser tests run the real extractors in headless Chromium
against the fixtures and are skipped if Chromium isn't installed. On a machine where the
Playwright-managed browser isn't available, set `PRICECHECK_CHROMIUM` to a Chromium executable.
