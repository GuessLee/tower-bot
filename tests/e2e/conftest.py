from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import asyncpg
import httpx
import pytest

from tower_bot.bot import TowerBot
from tower_bot.config import load_config
from tower_bot.core.db import apply_migrations

REQUIRED = [
    "E2E_BOT_TOKEN",
    "E2E_DRIVER_TOKEN",
    "E2E_GUILD_ID",
    "E2E_NIGHT_CHANNEL_ID",
    "E2E_LIBRARY_CHANNEL_ID",
    "E2E_LOG_CHANNEL_ID",
    "E2E_ADMIN_ROLE_ID",
    "E2E_DATABASE_URL",
    "E2E_GITHUB_TOKEN",
]
API = "https://discord.com/api/v10"


def _env() -> dict[str, str]:
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        pytest.fail(f"e2e env missing: {missing}")
    e = os.environ
    return {
        "DISCORD_TOKEN": e["E2E_BOT_TOKEN"],
        "DATABASE_URL": e["E2E_DATABASE_URL"],
        "GITHUB_TOKEN": e["E2E_GITHUB_TOKEN"],
        "GUILD_ID": e["E2E_GUILD_ID"],
        "NIGHT_CHANNEL_ID": e["E2E_NIGHT_CHANNEL_ID"],
        "LIBRARY_CHANNEL_ID": e["E2E_LIBRARY_CHANNEL_ID"],
        "LOG_CHANNEL_ID": e["E2E_LOG_CHANNEL_ID"],
        "ADMIN_ROLE_ID": e["E2E_ADMIN_ROLE_ID"],
        "FEEDBACK_LABELS": "feedback,e2e-test",
    }


def embed_text(message: dict[str, Any]) -> str:
    """Flatten a Discord message's first embed (title, description, fields) into one
    search string for the polling assertions in Driver.wait_embed. Pure and
    synchronous on purpose, so it is unit-testable without a live gateway."""
    emb = (message.get("embeds") or [{}])[0]
    return " ".join(
        [emb.get("title", ""), emb.get("description", "")]
        + [f"{f['name']} {f['value']}" for f in emb.get("fields", [])]
    )


class Driver:
    """A second bot account acting as 'friends' via raw REST."""

    def __init__(self, token: str) -> None:
        self.c = httpx.AsyncClient(
            base_url=API, headers={"Authorization": f"Bot {token}"}, timeout=20
        )

    async def me(self) -> int:
        return int((await self.c.get("/users/@me")).json()["id"])

    async def react(self, channel: int, message: int, emoji: str) -> None:
        r = await self.c.put(f"/channels/{channel}/messages/{message}/reactions/{quote(emoji)}/@me")
        assert r.status_code == 204, r.text

    async def unreact(self, channel: int, message: int, emoji: str) -> None:
        r = await self.c.delete(
            f"/channels/{channel}/messages/{message}/reactions/{quote(emoji)}/@me"
        )
        assert r.status_code == 204, r.text

    async def message(self, channel: int, message: int) -> dict[str, Any]:
        r = await self.c.get(f"/channels/{channel}/messages/{message}")
        return dict(r.json()) if r.status_code == 200 else {}

    async def wait_embed(self, channel: int, message: int, needle: str, seconds: float = 20) -> str:
        text = ""
        try:
            async with asyncio.timeout(seconds):
                while True:
                    m = await self.message(channel, message)
                    text = embed_text(m)
                    if needle in text:
                        return text
                    await asyncio.sleep(1)
        except TimeoutError:
            raise AssertionError(f"{needle!r} never appeared; last: {text!r}") from None


@pytest.fixture
async def e2e_db() -> AsyncIterator[str]:
    url = os.environ["E2E_DATABASE_URL"]
    c = await asyncpg.connect(url)
    await apply_migrations(c)
    await c.execute(
        "truncate games, nights, night_candidates, polls, poll_options, pins,"
        " audit_log, feedback_outbox restart identity cascade"
    )
    await c.execute(
        "insert into games(name,name_norm,source) values"
        " ('Fall Guys','fallguys','seed'), ('Among Us','amongus','seed')"
    )
    await c.close()
    yield url


@pytest.fixture
async def bot(e2e_db: str) -> AsyncIterator[TowerBot]:
    cfg = load_config(_env())
    pool = await asyncpg.create_pool(cfg.database_url, min_size=1, max_size=4)
    b = TowerBot(cfg, pool)
    task = asyncio.create_task(b.start(cfg.discord_token))
    try:
        await asyncio.wait_for(b.wait_until_ready(), 60)
        yield b
    finally:
        # Always tear down, even if the test above raised: leaving the bot
        # connected or the pool open would leak a gateway session and
        # connections into the next test.
        with contextlib.suppress(BaseException):
            await b.close()
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        await pool.close()


@pytest.fixture
async def driver() -> AsyncIterator[Driver]:
    d = Driver(os.environ["E2E_DRIVER_TOKEN"])
    try:
        yield d
    finally:
        await d.c.aclose()
