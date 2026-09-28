import asyncpg

from tower_bot.core.db import apply_migrations, create_pool


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


async def test_create_pool_with_password(db_url: str) -> None:
    # Parse user/password from db_url: postgresql://postgres:postgres@host:port/db
    # Strip password and pass it separately to create_pool
    parts = db_url.split("://", 1)[1]  # Remove scheme
    user_pass, host_db = parts.split("@", 1)
    user = user_pass.split(":")[0]
    # Reconstruct URL without password
    url_without_password = f"postgresql://{user}@{host_db}"
    # Call create_pool with password parameter
    p = await create_pool(url_without_password, "postgres")
    try:
        async with p.acquire() as c:
            assert await c.fetchval("select 1") == 1
    finally:
        await p.close()
