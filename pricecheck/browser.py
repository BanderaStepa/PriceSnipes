"""One polite browser session: one page, one page load at a time, 3–6 s between loads."""
from __future__ import annotations

import logging
import os
import random
import re
import time
from pathlib import Path

from .consent import dismiss_consent

log = logging.getLogger(__name__)

GOTO_TIMEOUT_MS = 45_000
READY_TIMEOUT_MS = 10_000
RETRY_DELAY_S = 10
POLITE_DELAY_S = (3.0, 6.0)

CAPTCHA_RE = re.compile(
    r"captcha|Geben Sie die Zeichen unten ein|Robot Check|sind Sie ein Mensch", re.I
)


class Session:
    """Wraps a Playwright page. Use `Session.launch(...)` for real runs."""

    def __init__(self, page, run_dir: Path, polite_delay=POLITE_DELAY_S, retry_delay_s=RETRY_DELAY_S,
                 consent_timeout_s: float = 3.0, ready_timeout_ms: int = READY_TIMEOUT_MS):
        self.page = page
        self.ready_timeout_ms = ready_timeout_ms
        self.run_dir = Path(run_dir)
        self.polite_delay = polite_delay
        self.retry_delay_s = retry_delay_s
        self.consent_timeout_s = consent_timeout_s
        self._loads = 0
        self._pw = None
        self._ctx = None

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def launch(cls, profile_dir: Path, run_dir: Path, headed: bool = False) -> "Session":
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        kwargs = dict(
            headless=not headed,
            locale="de-DE",
            timezone_id="Europe/Berlin",
            viewport={"width": 1400, "height": 900},
        )
        exe = os.environ.get("PRICECHECK_CHROMIUM")
        if exe:
            kwargs["executable_path"] = exe
        ctx = pw.chromium.launch_persistent_context(str(profile_dir), **kwargs)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        s = cls(page, run_dir)
        s._pw, s._ctx = pw, ctx
        return s

    def close(self):
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    # ------------------------------------------------------------------ navigation
    def _polite_wait(self):
        if self._loads and self.polite_delay:
            d = random.uniform(*self.polite_delay)
            log.debug("waiting %.1f s before next page", d)
            time.sleep(d)
        self._loads += 1

    def open(self, url: str, ready_selector: str | None = None) -> str:
        """Load `url`. Returns "ok" or "failed". Captcha is checked separately by the readers."""
        self._polite_wait()
        log.info("GET %s", url)
        for attempt in (1, 2):
            try:
                resp = self.page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
                status = resp.status if resp else None
                if status and status >= 400 and not self.is_captcha():
                    raise RuntimeError(f"HTTP {status}")
                break
            except Exception as e:  # noqa: BLE001
                log.warning("load failed (attempt %d): %s", attempt, str(e).splitlines()[0][:200])
                if attempt == 2:
                    return "failed"
                time.sleep(self.retry_delay_s)
        try:
            dismiss_consent(self.page, self.consent_timeout_s)
        except Exception as e:  # noqa: BLE001
            log.debug("consent handling error: %s", e)
        if ready_selector:
            try:
                self.page.wait_for_selector(ready_selector, timeout=self.ready_timeout_ms)
            except Exception:
                log.warning("ready selector not found within %.0f s: %s", self.ready_timeout_ms / 1000, ready_selector)
        return "ok"

    def body_text(self) -> str:
        try:
            return self.page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        except Exception:
            return ""

    def title(self) -> str:
        try:
            return self.page.title()
        except Exception:
            return ""

    def is_captcha(self) -> bool:
        return bool(CAPTCHA_RE.search(self.body_text() + " " + self.title()))

    # ------------------------------------------------------------------ files
    def screenshot_element(self, locator, name: str) -> str | None:
        path = self.run_dir / name
        try:
            locator.scroll_into_view_if_needed(timeout=3000)
            locator.screenshot(path=str(path), timeout=10_000)
            return str(path)
        except Exception as e:  # noqa: BLE001
            log.warning("element screenshot failed (%s): %s", name, str(e).splitlines()[0][:200])
            return self.screenshot_viewport(name)

    def screenshot_viewport(self, name: str) -> str | None:
        path = self.run_dir / name
        try:
            self.page.screenshot(path=str(path), timeout=15_000)
            return str(path)
        except Exception as e:  # noqa: BLE001
            log.warning("viewport screenshot failed (%s): %s", name, e)
            return None

    def dump_debug(self, row_id: str) -> None:
        """Save page HTML and a full-page screenshot for a row that could not be parsed."""
        d = self.run_dir / "debug"
        d.mkdir(parents=True, exist_ok=True)
        try:
            (d / f"{row_id}.html").write_text(self.page.content(), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.warning("could not save debug HTML for %s: %s", row_id, e)
        try:
            self.page.screenshot(path=str(d / f"{row_id}_full.png"), full_page=True, timeout=20_000)
        except Exception as e:  # noqa: BLE001
            log.warning("could not save debug screenshot for %s: %s", row_id, e)
        log.info("debug files saved to %s", d)
