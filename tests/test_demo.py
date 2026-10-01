"""The README GIFs are recorded by driving the real cog (scripts/demo/make_demo.py).
These keep every scene recording cleanly, so a renamed command or a changed reply
breaks here instead of leaving stale how-to GIFs behind. No browser needed."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import asyncpg
import pytest

from scripts.demo.make_demo import SCENES, Studio, record

# What each scene must end on.
ENDS_WITH = {
    "01-plan-a-night": "In (3)",
    "02-poll-for-a-time": "Poll closed",
    "03-game-library": "Game library (9 games)",
    "04-change-of-plans": "Cancelled - Game Night",
    "05-reminders": "Game locked: **Overcooked 2**. Have fun!",
}


def test_every_scene_has_an_expected_ending() -> None:
    assert {key for key, _, _ in SCENES} == set(ENDS_WITH)


@pytest.mark.parametrize(("key", "title", "scene"), SCENES, ids=[s[0] for s in SCENES])
async def test_scene_records(
    pool: asyncpg.Pool, key: str, title: str, scene: Callable[[Studio], Awaitable[None]]
) -> None:
    frames = await record(pool, title, scene)  # raises if the bot logged an error
    assert len(frames) > 3
    assert all(ms > 0 for _, ms in frames)
    last = json.loads(frames[-1][0])
    assert last["title"] == title and last["caption"]
    assert ENDS_WITH[key] in json.dumps(last["items"], ensure_ascii=False)
