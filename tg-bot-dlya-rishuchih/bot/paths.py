"""Шляхи проєкту."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONTENT_PATH = PROJECT_ROOT / "content.yaml"


def project_path(value: str | Path) -> Path:
    """Відносний шлях рахується від папки проєкту (а не від папки, з якої запустили бота)."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path
