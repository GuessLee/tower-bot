from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher

import asyncpg

from tower_bot.gamenight.models import Game

MAX_NAME = 80
FUZZY = 0.85
FRESH = timedelta(days=14)


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


@dataclass(frozen=True)
class Added:
    game: Game


@dataclass(frozen=True)
class Exists:
    game: Game


@dataclass(frozen=True)
class PossibleDuplicate:
    game: Game


async def suggest(
    conn: asyncpg.Connection,
    name: str,
    by: int | None,
    *,
    force: bool = False,
    source: str = "discord",
) -> Added | Exists | PossibleDuplicate:
    clean = " ".join(name.split())
    if not clean or len(clean) > MAX_NAME:
        raise ValueError(f"Game names must be 1 to {MAX_NAME} characters.")
    norm = normalize(clean)
    if not norm:
        raise ValueError("Game names need at least one letter or number.")
    row = await conn.fetchrow("select * from games where name_norm=$1", norm)
    if row is not None:
        return await _existing_or_reactivated(conn, row)
    if not force:
        for r in await conn.fetch("select * from games where active"):
            if SequenceMatcher(None, norm, r["name_norm"]).ratio() >= FUZZY:
                return PossibleDuplicate(Game.from_row(r))
    row = await conn.fetchrow(
        "insert into games(name,name_norm,suggested_by,source) values($1,$2,$3,$4) "
        "on conflict (name_norm) do nothing returning *",
        clean,
        norm,
        by,
        source,
    )
    if row is None:
        # Lost a race: another concurrent suggest() inserted this name_norm first.
        row = await conn.fetchrow("select * from games where name_norm=$1", norm)
        assert row is not None
        return await _existing_or_reactivated(conn, row)
    return Added(Game.from_row(row))


async def _existing_or_reactivated(conn: asyncpg.Connection, row: asyncpg.Record) -> Exists | Added:
    if row["active"]:
        return Exists(Game.from_row(row))
    updated = await conn.fetchrow("update games set active=true where id=$1 returning *", row["id"])
    assert updated is not None
    return Added(Game.from_row(updated))


async def list_active(conn: asyncpg.Connection) -> list[Game]:
    return [
        Game.from_row(r)
        for r in await conn.fetch("select * from games where active order by lower(name)")
    ]


async def remove(conn: asyncpg.Connection, name: str) -> Game | None:
    row = await conn.fetchrow(
        "update games set active=false where name_norm=$1 and active returning *", normalize(name)
    )
    return Game.from_row(row) if row else None


async def pick_candidates(conn: asyncpg.Connection, now: datetime, limit: int) -> list[Game]:
    rows = await conn.fetch(
        """
        select * from games where active
        order by (created_at >= $1 and times_played = 0) desc,
                 case when created_at >= $1 and times_played = 0 then created_at end desc,
                 last_played asc nulls first,
                 lower(name)
        limit $2
        """,
        now - FRESH,
        limit,
    )
    return [Game.from_row(r) for r in rows]


async def record_played(conn: asyncpg.Connection, game_id: int, at: datetime) -> None:
    await conn.execute(
        "update games set last_played=$2, times_played=times_played+1 where id=$1", game_id, at
    )
