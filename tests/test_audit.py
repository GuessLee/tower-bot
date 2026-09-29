import json
from datetime import datetime

import asyncpg

from tower_bot.core.audit import audit


async def test_audit_inserts_row(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        now = datetime(2026, 9, 28, 12, 30, 45)
        await audit(
            conn,
            actor_id=123,
            action="test_action",
            target="test_target",
            timestamp=now,
            count=5,
        )

        row = await conn.fetchrow(
            "select action, target, detail from audit_log "
            "where id = (select max(id) from audit_log)"
        )
        assert row is not None
        assert row["action"] == "test_action"
        assert row["target"] == "test_target"
        detail = json.loads(row["detail"])
        assert detail["timestamp"] == "2026-09-28 12:30:45"
        assert detail["count"] == 5
