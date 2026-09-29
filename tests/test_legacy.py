from pathlib import Path

import asyncpg
import pytest

from tower_bot.legacy import ROTATION, import_legacy, load_items


async def test_import_and_seed_idempotent(pool: asyncpg.Pool) -> None:
    items = [
        {"title": "Among Us", "score": 8},
        {"title": "Stardew Valley"},
        {"title": ""},
        {"title": None},
        {"nottitle": 1},
    ]
    async with pool.acquire() as c:
        r1 = await import_legacy(c, items)
        assert (r1.yamtrack_added, r1.skipped) == (2, 3)
        assert r1.seeded == len(ROTATION) - 1  # Among Us already came from yamtrack
        r2 = await import_legacy(c, items)
        assert (r2.yamtrack_added, r2.seeded) == (0, 0)
        rows = await c.fetch("select name, source from games order by name")
    names = {r["name"]: r["source"] for r in rows}
    assert names["Among Us"] == "yamtrack" and names["Fall Guys"] == "seed"
    assert names["Stardew Valley"] == "yamtrack"


async def test_empty_export_seeds_rotation(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as c:
        report = await import_legacy(c, [])
        assert report.yamtrack_added == 0
        assert report.seeded == 5
        assert report.skipped == 0
        rows = await c.fetch("select name, source from games order by name")
    names = {r["name"]: r["source"] for r in rows}
    assert len(names) == 5
    for name in ROTATION:
        assert names[name] == "seed"


def test_load_items_valid_list(tmp_path: Path) -> None:
    f = tmp_path / "items.json"
    f.write_text('[{"title": "Test Game"}]')
    items = load_items(str(f))
    assert items == [{"title": "Test Game"}]


def test_load_items_rejects_non_list(tmp_path: Path) -> None:
    f = tmp_path / "items.json"
    f.write_text('{"a": 1}')
    with pytest.raises(ValueError, match="JSON root must be a list"):
        load_items(str(f))
