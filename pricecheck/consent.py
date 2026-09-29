"""Cookie banners: reject / "necessary only", never accept.

Searches the main page and all iframes. Playwright role locators pierce open shadow DOM,
which covers Usercentrics-style banners.
"""
from __future__ import annotations

import logging
import re
import time

log = logging.getLogger(__name__)

REJECT_RE = re.compile(
    r"alle ablehnen|ablehnen|nur notwendige|nur erforderliche|nur essenzielle|"
    r"weiter ohne einwilligung|reject all|decline|necessary only",
    re.I,
)
# Safety net: never click anything that sounds like consenting.
ACCEPT_RE = re.compile(r"akzept|accept|zustimm|einverstanden|agree|allow|erlauben|annehmen", re.I)
CLOSE_RE = re.compile(r"^\s*(schließen|schliessen|close|x|×|✕|✖)\s*$", re.I)
BANNER_TEXT_RE = re.compile(r"cookie|einwilligung|datenschutz|consent|privacy", re.I)

DEFAULT_TIMEOUT_S = 3.0


def _try_click(locator) -> bool:
    try:
        n = locator.count()
    except Exception:
        return False
    for i in range(n):
        el = locator.nth(i)
        try:
            if not el.is_visible():
                continue
            name = (el.inner_text(timeout=500) or el.get_attribute("aria-label") or "").strip()
            if name and ACCEPT_RE.search(name):
                continue
            el.click(timeout=2000)
            log.info("cookie banner: clicked %r", name or "<button>")
            return True
        except Exception:
            continue
    return False


def _reject_in_frame(frame) -> bool:
    for role in ("button", "link"):
        if _try_click(frame.get_by_role(role, name=REJECT_RE)):
            return True
    return False


def _close_in_frame(frame) -> bool:
    """Close button inside a visible consent dialog (only used when no reject option exists)."""
    try:
        dialogs = frame.locator("[role=dialog], [aria-modal=true], #usercentrics-root, [id*=consent], [class*=consent], [id*=cookie], [class*=cookie]")
        for i in range(min(dialogs.count(), 10)):
            d = dialogs.nth(i)
            if not d.is_visible():
                continue
            try:
                txt = d.inner_text(timeout=500)
            except Exception:
                txt = ""
            if txt and not BANNER_TEXT_RE.search(txt):
                continue
            if _try_click(d.get_by_role("button", name=CLOSE_RE)):
                return True
            if _try_click(d.locator("[aria-label='Schließen'], [aria-label='Close'], [title='Schließen'], [title='Close']")):
                return True
    except Exception:
        pass
    return False


def dismiss_consent(page, timeout_s: float = DEFAULT_TIMEOUT_S) -> bool:
    """Try for up to `timeout_s` to reject a cookie banner. Returns True if something was clicked."""
    deadline = time.monotonic() + timeout_s
    while True:
        for frame in list(page.frames):
            if _reject_in_frame(frame):
                page.wait_for_timeout(500)
                return True
        if time.monotonic() >= deadline:
            break
        page.wait_for_timeout(400)
    for frame in list(page.frames):
        if _close_in_frame(frame):
            page.wait_for_timeout(500)
            return True
    return False
