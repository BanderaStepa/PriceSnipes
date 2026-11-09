"""Спільні фікстури тестів."""

from __future__ import annotations

import copy
from collections.abc import Callable

import pytest
import yaml

from bot.content import parse_content
from tests.harness import REFERENCE_CONTENT_PATH, Harness, make_harness

_RAW = yaml.safe_load(REFERENCE_CONTENT_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def raw_content() -> dict:
    """Еталонний контент тестів як словник (копія — тест може її змінювати)."""
    return copy.deepcopy(_RAW)


@pytest.fixture
async def make(tmp_path):
    """Фабрика тестового бота: make(зміна_контенту, manager_chat_id=…)."""
    created: list[Harness] = []

    async def factory(change: Callable[[dict], None] | None = None, **kwargs) -> Harness:
        """change(raw) змінює копію еталонного контенту; kwargs — manager_chat_id, admin_ids, start…"""
        raw = copy.deepcopy(_RAW)
        if change is not None:
            change(raw)
        harness = await make_harness(parse_content(raw), tmp_path / f"bot{len(created)}.db", **kwargs)
        created.append(harness)
        return harness

    yield factory
    for harness in created:
        await harness.close()


@pytest.fixture
async def harness(make) -> Harness:
    """Бот з еталонним контентом тестів."""
    return await make()
