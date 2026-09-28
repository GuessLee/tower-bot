from __future__ import annotations

from importlib.resources import files

import asyncpg

_LOCK = 727001


async def apply_migrations(conn: asyncpg.Connection) -> list[str]:
    await conn.execute(
        "create table if not exists schema_migrations("
        "version text primary key, applied_at timestamptz not null default now())"
    )
    await conn.execute("select pg_advisory_lock($1)", _LOCK)
    try:
        done = {r["version"] for r in await conn.fetch("select version from schema_migrations")}
        scripts = sorted(
            (p for p in files("tower_bot.migrations").iterdir() if p.name.endswith(".sql")),
            key=lambda p: p.name,
        )
        applied: list[str] = []
        for script in scripts:
            if script.name in done:
                continue
            async with conn.transaction():
                await conn.execute(script.read_text())
                await conn.execute("insert into schema_migrations(version) values($1)", script.name)
            applied.append(script.name)
        return applied
    finally:
        await conn.execute("select pg_advisory_unlock($1)", _LOCK)


async def create_pool(url: str, password: str | None) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(url, password=password, min_size=1, max_size=5)
    assert pool is not None
    return pool
