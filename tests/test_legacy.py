import asyncpg

from tower_bot.legacy import ROTATION, import_legacy


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
