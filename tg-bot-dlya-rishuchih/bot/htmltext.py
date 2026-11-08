"""Тексти з content.yaml у форматі Telegram-HTML: перевірка, екранування, підстановки {…}."""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

# Теги, які підтримує Telegram у режимі parse_mode=HTML.
ALLOWED_TAGS = frozenset(
    {
        "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
        "span", "tg-spoiler", "a", "tg-emoji", "code", "pre", "blockquote",
    }
)
# Поширені теги з веб-HTML, яких Telegram не знає, і що порадити замість них.
_HINTS = {
    "br": "для нового рядка просто перенесіть текст на новий рядок",
    "p": "абзаци робіть порожнім рядком",
    "div": "просто перенесіть текст на новий рядок",
    "h1": "використайте <b>…</b>",
    "h2": "використайте <b>…</b>",
    "h3": "використайте <b>…</b>",
    "ul": "списки пишіть рядками з «•» або «✅» на початку",
    "ol": "списки пишіть рядками з «1.», «2.» на початку",
    "li": "списки пишіть рядками з «•» або «✅» на початку",
    "font": "кольори в Telegram недоступні",
}

_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)((?:\s[^<>]*)?)(/?)>")
_ENTITY_RE = re.compile(r"&(?:lt|gt|amp|quot|#\d{1,7}|#[xX][0-9a-fA-F]{1,6});")
_BARE_AMP_RE = re.compile(r"&(?!(?:lt|gt|amp|quot|#\d{1,7}|#[xX][0-9a-fA-F]{1,6});)")
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ANY_TAG_RE = re.compile(r"<[^<>]*>")


@dataclass
class HtmlCheck:
    """Результат перевірки: виправлений текст і список помилок."""

    text: str
    errors: list[str] = field(default_factory=list)


def prepare_html(text: str) -> HtmlCheck:
    """Перевіряє Telegram-HTML і екранує символи < > &, які не є частиною тегу.

    Непідтримувані або незакриті теги — помилки (бот такий текст не надішле).
    Зайві «<», «>» і «&» у звичайному тексті виправляються автоматично.
    """
    out: list[str] = []
    errors: list[str] = []
    stack: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "<":
            m = _TAG_RE.match(text, i)
            if m is None:
                out.append("&lt;")
                i += 1
                continue
            closing, name, attrs, self_closing = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
            if name not in ALLOWED_TAGS:
                hint = _HINTS.get(name)
                errors.append(
                    f"тег <{name}> не підтримується Telegram" + (f" — {hint}" if hint else "")
                )
            elif self_closing:
                errors.append(f"тег <{name}/> не може бути порожнім — пишіть <{name}>текст</{name}>")
            elif closing:
                if name in stack:
                    while stack and stack[-1] != name:
                        errors.append(f"тег <{stack.pop()}> не закрито перед </{name}>")
                    stack.pop()
                else:
                    errors.append(f"зайвий закривальний тег </{name}> (немає відповідного <{name}>)")
            else:
                if name == "a" and "href" not in attrs.lower():
                    errors.append('у тегу <a> потрібне посилання: <a href="https://…">текст</a>')
                if name == "span" and "tg-spoiler" not in attrs:
                    errors.append('тег <span> підтримується лише як <span class="tg-spoiler">…</span>')
                stack.append(name)
            out.append(_BARE_AMP_RE.sub("&amp;", m.group(0)))
            i = m.end()
            continue
        if ch == ">":
            out.append("&gt;")
            i += 1
            continue
        if ch == "&":
            m = _ENTITY_RE.match(text, i)
            if m:
                out.append(m.group(0))
                i = m.end()
            else:
                out.append("&amp;")
                i += 1
            continue
        out.append(ch)
        i += 1
    if stack:
        errors.append("не закрито тег(и): " + ", ".join(f"<{name}>" for name in stack))
    return HtmlCheck("".join(out), errors)


def placeholders(text: str) -> set[str]:
    """Назви підстановок {…}, що трапляються в тексті."""
    return set(_PLACEHOLDER_RE.findall(text))


def fill(template: str, values: Mapping[str, str]) -> str:
    """Замінює відомі підстановки {…}; невідомі лишає як є (жодних помилок через «{» у тексті)."""
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def esc(value: object) -> str:
    """Екранує звичайний текст для вставки в HTML."""
    return html.escape(str(value), quote=True)


def strip_tags(text: str) -> str:
    """HTML → звичайний текст (для запасного надсилання без форматування і підрахунку довжини)."""
    return html.unescape(_ANY_TAG_RE.sub("", text))


def has_tags(text: str) -> bool:
    """Чи схожий текст на HTML-розмітку (для полів, де HTML не підтримується)."""
    return bool(re.search(r"</?[a-zA-Z][a-zA-Z0-9-]*(?:\s[^<>]*)?/?>", text))
