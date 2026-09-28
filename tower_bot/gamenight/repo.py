from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import asyncpg

from tower_bot.gamenight.models import Candidate, Game, Night, Poll, PollOption

_NIGHT_FIELDS = {
    "message_id",
    "event_id",
    "starts_at",
    "status",
    "chosen_game_id",
    "chosen_override",
    "reminded_24h",
    "reminded_1h",
}
_POLL_FIELDS = {"message_id", "status", "night_id"}


def _set_clause(fields: dict[str, Any], allowed: set[str]) -> tuple[str, list[Any]]:
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"cannot update {bad}")
    keys = list(fields)
    return ", ".join(f"{k}=${i + 2}" for i, k in enumerate(keys)), [fields[k] for k in keys]


async def insert_night(
    c: asyncpg.Connection,
    *,
    guild_id: int,
    channel_id: int,
    starts_at: datetime,
    note: str | None,
    poster_id: int,
    reminded_24h: bool,
    reminded_1h: bool,
) -> Night:
    r = await c.fetchrow(
        "insert into nights(guild_id,channel_id,starts_at,note,poster_id,reminded_24h,reminded_1h)"
        " values($1,$2,$3,$4,$5,$6,$7) returning *",
        guild_id,
        channel_id,
        starts_at,
        note,
        poster_id,
        reminded_24h,
        reminded_1h,
    )
    return Night.from_row(r)


async def delete_night(c: asyncpg.Connection, night_id: int) -> None:
    await c.execute("delete from nights where id=$1", night_id)


async def get_night(c: asyncpg.Connection, night_id: int) -> Night | None:
    r = await c.fetchrow("select * from nights where id=$1", night_id)
    return Night.from_row(r) if r else None


async def night_by_message(c: asyncpg.Connection, message_id: int) -> Night | None:
    r = await c.fetchrow("select * from nights where message_id=$1", message_id)
    return Night.from_row(r) if r else None


async def update_night(c: asyncpg.Connection, night_id: int, **fields: Any) -> Night:
    clause, vals = _set_clause(fields, _NIGHT_FIELDS)
    r = await c.fetchrow(f"update nights set {clause} where id=$1 returning *", night_id, *vals)
    return Night.from_row(r)


async def insert_candidates(
    c: asyncpg.Connection, night_id: int, cands: Sequence[Candidate]
) -> None:
    await c.executemany(
        "insert into night_candidates(night_id,position,game_id,emoji) values($1,$2,$3,$4)",
        [(night_id, x.position, x.game.id, x.emoji) for x in cands],
    )


async def candidates(c: asyncpg.Connection, night_id: int) -> list[Candidate]:
    rows = await c.fetch(
        "select nc.position, nc.emoji, g.* from night_candidates nc join games g on g.id=nc.game_id"
        " where nc.night_id=$1 order by nc.position",
        night_id,
    )
    return [Candidate(r["position"], r["emoji"], Game.from_row(r)) for r in rows]


async def next_open(c: asyncpg.Connection, now: datetime) -> Night | None:
    r = await c.fetchrow(
        "select * from nights where status='open' and message_id is not null and starts_at > $1"
        " order by starts_at limit 1",
        now,
    )
    return Night.from_row(r) if r else None


async def open_nights(c: asyncpg.Connection) -> list[Night]:
    return [
        Night.from_row(r)
        for r in await c.fetch(
            "select * from nights where status='open' and message_id is not null order by starts_at"
        )
    ]


async def due_locks(c: asyncpg.Connection, now: datetime) -> list[Night]:
    return [
        Night.from_row(r)
        for r in await c.fetch(
            "select * from nights where status='open' and message_id is not null"
            " and starts_at <= $1 order by starts_at",
            now,
        )
    ]


async def due_reminders(c: asyncpg.Connection, now: datetime) -> list[tuple[Night, str]]:
    rows = await c.fetch(
        """
        select *,
          (not reminded_24h and starts_at - interval '24 hours' <= $1) as due24,
          (not reminded_1h and starts_at - interval '1 hour' <= $1) as due1
        from nights
        where status='open' and message_id is not null and starts_at > $1
          and ((not reminded_24h and starts_at - interval '24 hours' <= $1)
            or (not reminded_1h and starts_at - interval '1 hour' <= $1))
        order by starts_at
        """,
        now,
    )
    out: list[tuple[Night, str]] = []
    for r in rows:
        out.append((Night.from_row(r), "1h" if r["due1"] else "24h"))
    return out


async def _poll_from_row(c: asyncpg.Connection, r: Any) -> Poll:
    opts = await c.fetch("select * from poll_options where poll_id=$1 order by position", r["id"])
    return Poll(
        r["id"],
        r["guild_id"],
        r["channel_id"],
        r["message_id"],
        r["poster_id"],
        r["status"],
        r["night_id"],
        tuple(PollOption(o["position"], o["emoji"], o["starts_at"]) for o in opts),
    )


async def insert_poll(
    c: asyncpg.Connection,
    *,
    guild_id: int,
    channel_id: int,
    poster_id: int,
    options: Sequence[tuple[str, datetime]],
) -> Poll:
    r = await c.fetchrow(
        "insert into polls(guild_id,channel_id,poster_id) values($1,$2,$3) returning *",
        guild_id,
        channel_id,
        poster_id,
    )
    assert r is not None
    await c.executemany(
        "insert into poll_options(poll_id,position,emoji,starts_at) values($1,$2,$3,$4)",
        [(r["id"], i + 1, e, t) for i, (e, t) in enumerate(options)],
    )
    return await _poll_from_row(c, r)


async def get_poll(c: asyncpg.Connection, poll_id: int) -> Poll | None:
    r = await c.fetchrow("select * from polls where id=$1", poll_id)
    return await _poll_from_row(c, r) if r else None


async def poll_by_message(c: asyncpg.Connection, message_id: int) -> Poll | None:
    r = await c.fetchrow("select * from polls where message_id=$1", message_id)
    return await _poll_from_row(c, r) if r else None


async def latest_open_poll(c: asyncpg.Connection) -> Poll | None:
    r = await c.fetchrow(
        "select * from polls where status='open' and message_id is not null"
        " order by created_at desc, id desc limit 1"
    )
    return await _poll_from_row(c, r) if r else None


async def open_polls(c: asyncpg.Connection) -> list[Poll]:
    rows = await c.fetch("select * from polls where status='open' and message_id is not null")
    return [await _poll_from_row(c, r) for r in rows]


async def update_poll(c: asyncpg.Connection, poll_id: int, **fields: Any) -> Poll:
    clause, vals = _set_clause(fields, _POLL_FIELDS)
    r = await c.fetchrow(f"update polls set {clause} where id=$1 returning *", poll_id, *vals)
    return await _poll_from_row(c, r)


async def get_pin(c: asyncpg.Connection, kind: str) -> tuple[int, int] | None:
    r = await c.fetchrow("select channel_id, message_id from pins where kind=$1", kind)
    return (r["channel_id"], r["message_id"]) if r else None


async def upsert_pin(c: asyncpg.Connection, kind: str, channel_id: int, message_id: int) -> None:
    await c.execute(
        "insert into pins(kind,channel_id,message_id) values($1,$2,$3)"
        " on conflict (kind) do update set channel_id=$2, message_id=$3",
        kind,
        channel_id,
        message_id,
    )
