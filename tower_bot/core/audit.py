from __future__ import annotations

import json

import asyncpg


async def audit(
    conn: asyncpg.Connection,
    actor_id: int | None,
    action: str,
    target: str | None,
    **detail: object,
) -> None:
    await conn.execute(
        "insert into audit_log(actor_id, action, target, detail) values($1,$2,$3,$4::jsonb)",
        actor_id,
        action,
        target,
        json.dumps(detail, default=str),
    )
