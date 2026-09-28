import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator

import asyncpg
import pytest

from tower_bot.core.db import apply_migrations

BASE = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
TABLES = "games, nights, night_candidates, polls, poll_options, pins, audit_log, feedback_outbox"


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    name = f"tb_test_{uuid.uuid4().hex[:8]}"
    url = BASE.rsplit("/", 1)[0] + "/" + name

    async def setup() -> None:
        c = await asyncpg.connect(BASE)
        await c.execute(f'create database "{name}"')
        await c.close()
        c = await asyncpg.connect(url)
        await apply_migrations(c)
        await c.close()

    async def teardown() -> None:
        c = await asyncpg.connect(BASE)
        await c.execute(f'drop database if exists "{name}" with (force)')
        await c.close()

    asyncio.run(setup())
    yield url
    asyncio.run(teardown())


@pytest.fixture
async def pool(db_url: str) -> AsyncIterator[asyncpg.Pool]:
    p = await asyncpg.create_pool(db_url, min_size=1, max_size=4)
    async with p.acquire() as c:
        await c.execute(f"truncate {TABLES} restart identity cascade")
    yield p
    await p.close()
