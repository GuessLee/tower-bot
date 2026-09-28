import asyncpg

from tower_bot.core.db import apply_migrations


async def test_migrations_idempotent(db_url: str) -> None:
    c = await asyncpg.connect(db_url)
    try:
        assert await apply_migrations(c) == []  # session fixture already applied
        tables = {
            r["tablename"]
            for r in await c.fetch("select tablename from pg_tables where schemaname='public'")
        }
        assert {
            "games",
            "nights",
            "night_candidates",
            "polls",
            "poll_options",
            "pins",
            "audit_log",
            "feedback_outbox",
            "schema_migrations",
        } <= tables
        assert await c.fetchval("select count(*) from schema_migrations") == 1
    finally:
        await c.close()


async def test_pool_fixture_is_clean(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as c:
        await c.execute("insert into games(name, name_norm) values('A','a')")
        assert await c.fetchval("select count(*) from games") == 1
