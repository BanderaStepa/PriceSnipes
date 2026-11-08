"""Спільні фікстури тестів."""

from __future__ import annotations

import copy

import pytest
import yaml

from bot.paths import DEFAULT_CONTENT_PATH

_RAW = yaml.safe_load(DEFAULT_CONTENT_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def raw_content() -> dict:
    """Справжній content.yaml як словник (копія — тест може її змінювати)."""
    return copy.deepcopy(_RAW)
