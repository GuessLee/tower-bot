import asyncio
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from tower_bot.gamenight import games
from tower_bot.gamenight.games import Added, Exists, PossibleDuplicate

NOW = datetime(2026, 9, 28, 21, 0, tzinfo=UTC)


def test_normalize() -> None:
    assert games.normalize("Overcooked! 2") == "overcooked2"
    assert games.normalize("  Among   Us ") == "amongus"
    assert games.normalize("🎮") == ""


async def test_suggest_add_exists_and_fuzzy(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as c:
        r = await games.suggest(c, "  Rocket   League ", 7)
        assert isinstance(r, Added) and r.game.name == "Rocket League"
        assert isinstance(await games.suggest(c, "rocket league!", 8), Exists)
        dup = await games.suggest(c, "Rocket Leage", 8)
        assert isinstance(dup, PossibleDuplicate) and dup.game.name == "Rocket League"
        forced = await games.suggest(c, "Rocket Leage", 8, force=True)
        assert isinstance(forced, Added)


@pytest.mark.parametrize("bad", ["", "   ", "🎮🎮", "x" * 81])
async def test_suggest_rejects(pool: asyncpg.Pool, bad: str) -> None:
    async with pool.acquire() as c:
        with pytest.raises(ValueError):
            await games.suggest(c, bad, 1)


async def test_remove_then_resuggest_reactivates(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as c:
        await games.suggest(c, "Fall Guys", 1)
        removed = await games.remove(c, "fall guys")
        assert removed is not None and not removed.active
        assert await games.list_active(c) == []
        assert await games.remove(c, "nope") is None
        again = await games.suggest(c, "Fall Guys", 2)
        assert isinstance(again, Added) and again.game.active


async def test_pick_candidates_order(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as c:

        async def add(name: str, created: datetime, played: datetime | None, times: int) -> None:
            await c.execute(
                "insert into games(name,name_norm,created_at,last_played,times_played) "
                "values($1,$2,$3,$4,$5)",
                name,
                games.normalize(name),
                created,
                played,
                times,
            )

        old = NOW - timedelta(days=60)
        await add("Old Played Long Ago", old, NOW - timedelta(days=30), 2)
        await add("Old Played Recently", old, NOW - timedelta(days=2), 5)
        await add("Old Never Played", old, None, 0)
        await add("Fresh Suggestion", NOW - timedelta(days=1), None, 0)
        await add("Fresher Suggestion", NOW - timedelta(hours=1), None, 0)
        await c.execute("insert into games(name,name_norm,active) values('Gone','gone',false)")
        picked = [g.name for g in await games.pick_candidates(c, NOW, 4)]
        assert picked == [
            "Fresher Suggestion",
            "Fresh Suggestion",
            "Old Never Played",
            "Old Played Long Ago",
        ]


async def test_concurrent_same_suggestion(pool: asyncpg.Pool) -> None:
    conns = [await pool.acquire() for _ in range(5)]
    try:
        results = await asyncio.gather(
            *(games.suggest(c, "Among Us", i) for i, c in enumerate(conns))
        )
    finally:
        for c in conns:
            await pool.release(c)
    added = [r for r in results if isinstance(r, Added)]
    exists = [r for r in results if isinstance(r, Exists)]
    assert len(added) == 1
    assert len(exists) == 4


async def test_record_played(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as c:
        r = await games.suggest(c, "Among Us", None)
        assert isinstance(r, Added)
        await games.record_played(c, r.game.id, NOW)
        g = (await games.list_active(c))[0]
        assert g.times_played == 1 and g.last_played == NOW
