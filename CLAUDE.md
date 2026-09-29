# Notes for Claude Code sessions

- This repo is a **no-AI price checker** (see `BUILD_SPEC.md`). Keep it deterministic: plain
  parsing rules, no model calls, no guessing. A row without a readable price says so; it never
  gets an estimated price.
- **Never add bot-evasion or CAPTCHA solving**: no stealth plugins, fingerprint spoofing, proxies
  or automated CAPTCHA handling. A CAPTCHA page means `blocked by site (captcha)`.
- **Bases never change.** Don't edit any `"base"` or `"base_total"` in `config.json` unless the user
  explicitly asks for that exact change.
- When the user says **"I bought X for €Y at Z"**, add one entry to `config.json` → `purchases`
  with today's date and change nothing else:
  `{"item_group":"<item id or group>","price":Y,"shop":"Z","date":"YYYY-MM-DD"}`.
  `item_group` is `"1"`–`"5"` or `RAM_PATRIOT` / `RAM_TFORCE` / `COOLER` / `CASE`.
- Run `pytest` after every change (the browser tests need Chromium; in the cloud container
  `tests/conftest.py` falls back to `/opt/pw-browsers/chromium`).
- Fixing a broken extractor: the user commits `runs/<stamp>/debug/<row>.html` (+ `_full.png`).
  Selectors and phrases are constants at the top of `pricecheck/sites/{geizhals,idealo,amazon}.py`.
  Add a trimmed copy of the real HTML as a fixture in `tests/fixtures/` with a test, then fix.
- The cloud session can't reach the shops. Test only against fixtures; the first live run happens on
  the user's Windows PC.
