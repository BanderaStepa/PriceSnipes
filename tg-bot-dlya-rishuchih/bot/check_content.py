"""Перевірка content.yaml без запуску бота.

    python -m bot.check_content            # перевірити content.yaml (або файл із CONTENT_PATH)
    python -m bot.check_content інший.yaml # перевірити інший файл

Код виходу 0 — помилок немає, 1 — є помилки (так само перевіряється під час збірки на хостингу).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .content import ContentError, load_content
from .paths import DEFAULT_CONTENT_PATH, project_path
from .timeutils import format_duration


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    args = sys.argv[1:] if argv is None else argv
    if args:
        path = Path(args[0])
    elif os.environ.get("CONTENT_PATH"):
        path = project_path(os.environ["CONTENT_PATH"])
    else:
        path = DEFAULT_CONTENT_PATH

    try:
        content = load_content(path)
    except ContentError as e:
        print(f"ПОМИЛКА: у файлі {path} є помилки — з ним бот не запуститься.\n")
        print(e.report())
        if e.warnings:
            print(f"\nПопередження ({len(e.warnings)}):")
            for warning in e.warnings:
                print(f"  • {warning}")
        return 1

    s = content.settings
    print(f"OK: {path} — помилок немає.\n")
    print(f"Тривалість пропозиції: {format_duration(s.offer_duration)}")
    print(f"Часовий пояс: {s.timezone.key}")
    if s.timer_update_every and s.timer_fast_every:
        print(f"Живий таймер в офері: поки людина в чаті — кожні {format_duration(s.timer_fast_every)} "
              f"(ще {format_duration(s.timer_fast_for)} після її останньої дії), "
              f"інакше раз на {format_duration(s.timer_update_every)}")
    elif s.timer_update_every:
        print(f"Живий таймер в офері: оновлюється раз на {format_duration(s.timer_update_every)}")
    else:
        print("Живий таймер в офері: вимкнено (показується залишок на момент надсилання)")
    if s.packages_after_expiry:
        print("Після завершення пропозиції: пакети доступні й далі")
    else:
        print("Після завершення пропозиції: пакети й оплата недоступні")
    if not content.messages.get("expired"):
        print("Відповідь після завершення: опис курсу (як «Інформація про курс»), без таймера й подарунка")
    print("Повідомлення в момент завершення: " + ("так" if s.send_expired_notice else "ні"))
    print("Тихі години (без звуку): " + (s.silent_hours.describe() if s.silent_hours else "вимкнено"))

    print(f"\nПакети ({len(content.packages)}):")
    for number, package in enumerate(content.packages, start=1):
        link = package.payment_url or "без посилання на оплату"
        picture = f"  [картинка: {package.image}]" if package.image else ""
        print(f"  {number}. {package.name} — {package.price}  →  {link}{picture}")
    if not any(p.payment_url for p in content.packages):
        print("  Режим: інформаційний — посилань на оплату немає, кнопки оплати не показуються.")

    print(f"\nНагадування ({len(content.reminders)}):")
    for reminder in content.reminders:
        print(f"  • {reminder.id}: {reminder.describe()}")

    if content.warnings:
        print(f"\nПопередження ({len(content.warnings)}):")
        for warning in content.warnings:
            print(f"  • {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
