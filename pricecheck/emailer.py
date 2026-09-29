"""Gmail SMTP (smtp.gmail.com:465, SSL) with a Google app password."""
from __future__ import annotations

import io
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
MAX_ATTACH_BYTES = 20 * 1024 * 1024


class EmailConfigError(RuntimeError):
    pass


def credentials(config: dict) -> tuple[str, str, str]:
    """(from address, app password, to address) from the environment / .env."""
    addr = (os.environ.get("GMAIL_ADDRESS") or "").strip()
    pw = (os.environ.get("GMAIL_APP_PASSWORD") or "").replace(" ", "").strip()
    to = (os.environ.get("MAIL_TO") or config.get("mail_to") or "").strip()
    missing = [n for n, v in (("GMAIL_ADDRESS", addr), ("GMAIL_APP_PASSWORD", pw), ("MAIL_TO / mail_to", to)) if not v]
    if missing:
        raise EmailConfigError("missing " + ", ".join(missing) + " (set them in .env)")
    return addr, pw, to


def _downscale(data: bytes, factor: float) -> tuple[bytes, str]:
    from PIL import Image

    im = Image.open(io.BytesIO(data))
    im = im.convert("RGB")
    w, h = im.size
    im = im.resize((max(1, int(w * factor)), max(1, int(h * factor))))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=75, optimize=True)
    return buf.getvalue(), "jpg"


def prepare_attachments(files: list[tuple[str, str]], limit: int = MAX_ATTACH_BYTES) -> list[tuple[str, bytes, str]]:
    """[(attachment name, path)] -> [(name, bytes, subtype)], downscaled to JPEG if over `limit`."""
    items = []
    for name, path in files:
        try:
            items.append([name, Path(path).read_bytes(), Path(name).suffix.lstrip(".").lower() or "png"])
        except OSError as e:
            log.warning("attachment %s unreadable: %s", path, e)
    total = sum(len(b) for _, b, _ in items)
    factor = 1.0
    while total > limit and factor > 0.1:
        factor *= 0.7
        log.info("attachments are %.1f MB, downscaling to %d%%", total / 1e6, factor * 100)
        try:
            new = []
            for name, data, sub in items:
                d2, s2 = _downscale(data, factor)
                new.append([str(Path(name).with_suffix("." + s2)), d2, s2])
        except ImportError:
            log.warning("Pillow not installed; dropping attachments to stay under the size limit")
            while items and total > limit:
                items.pop()
                total = sum(len(b) for _, b, _ in items)
            break
        total = sum(len(b) for _, b, _ in new)
        if total <= limit or factor <= 0.1:
            items = new
    return [(n, b, "jpeg" if s in ("jpg", "jpeg") else s) for n, b, s in items]


def build_message(subject: str, html_body: str, text_body: str, sender: str, to: str,
                  attachments: list[tuple[str, str]] | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(text_body)
    msg.add_alternative(f'<!doctype html><html><body>{html_body}</body></html>', subtype="html")
    for name, data, sub in prepare_attachments(attachments or []):
        msg.add_attachment(data, maintype="image", subtype=sub, filename=name)
    return msg


def send(msg: EmailMessage, sender: str, password: str) -> None:
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=60) as s:
        s.login(sender, password)
        s.send_message(msg)
    log.info("email sent to %s", msg["To"])


def send_test(config: dict) -> None:
    sender, pw, to = credentials(config)
    msg = EmailMessage()
    msg["Subject"] = "White Build price check – test email"
    msg["From"] = sender
    msg["To"] = to
    msg.set_content("This is a test email from the White Build price checker. If you can read this, "
                    "Gmail sending works.")
    send(msg, sender, pw)

