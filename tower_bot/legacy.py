from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import asyncpg

from tower_bot.config import load_config
from tower_bot.core.db import apply_migrations, create_pool
from tower_bot.gamenight import games

ROTATION: tuple[str, ...] = (
    "Fall Guys",
    "Fortnite Zero Build",
    "Rocket League",
    "Among Us",
    "Overcooked",
)


@dataclass(frozen=True)
class ImportReport:
    yamtrack_added: int
    seeded: int
    skipped: int


def load_items(path: str) -> list[dict]:  # type: ignore[type-arg]
    """Load items from a JSON file, validating that the root is a list."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"JSON root must be a list, got {type(data).__name__}")
    return data


async def import_legacy(
    conn: asyncpg.Connection, items: Sequence[Mapping[str, object]]
) -> ImportReport:
    added = seeded = skipped = 0
    for it in items:
        title = str(it.get("title") or "").strip()
        if not title:
            skipped += 1
            continue
        try:
            r = await games.suggest(conn, title, None, force=True, source="yamtrack")
        except ValueError:
            skipped += 1
            continue
        if isinstance(r, games.Added):
            added += 1
    for name in ROTATION:
        r = await games.suggest(conn, name, None, force=True, source="seed")
        if isinstance(r, games.Added):
            seeded += 1
    return ImportReport(added, seeded, skipped)


async def _main(path: str) -> None:
    items = [] if path == "-" else load_items(path)
    cfg = load_config(os.environ)
    pool = await create_pool(cfg.database_url, cfg.db_password)
    async with pool.acquire() as c:
        c_conn = cast(asyncpg.Connection, c)
        await apply_migrations(c_conn)
        report = await import_legacy(c_conn, items)
    await pool.close()
    print(report)


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1] if len(sys.argv) > 1 else "-"))
