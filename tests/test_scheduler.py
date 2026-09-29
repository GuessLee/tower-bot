from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from tests.fakes import FakeGateway
from tower_bot.core.clock import FakeClock
from tower_bot.gamenight import games
from tower_bot.gamenight.scheduler import tick
from tower_bot.gamenight.service import GameNightService, Settings

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 28, 21, 0, tzinfo=UTC)
S = Settings(100, 200, 300, 400, 9, TZ)


async def no_sleep(_: float) -> None:
    return None


async def test_tick_reminds_locks_and_survives_errors(pool: asyncpg.Pool) -> None:
    gw, clock = FakeGateway(), FakeClock(NOW)
    async with pool.acquire() as c:
        await games.suggest(c, "Fall Guys", None, force=True)
    svc = GameNightService(pool, gw, S, clock, retry_sleep=no_sleep)
    n = await svc.create_night(1, NOW + timedelta(days=2))

    calls: list[datetime] = []

    async def failing_retry(now: datetime) -> list[str]:
        calls.append(now)
        raise RuntimeError("github down")

    clock.set(n.starts_at - timedelta(hours=23))
    await tick(svc, clock.now(), failing_retry)
    assert len(gw.replies) == 1 and calls  # reminder sent, feedback error swallowed
    assert any(m.channel_id == 400 for m in gw.messages.values())  # error logged

    clock.set(n.starts_at + timedelta(minutes=1))
    await tick(svc, clock.now())
    async with pool.acquire() as c:
        assert await c.fetchval("select status from nights where id=$1", n.id) == "locked"


async def test_tick_reports_gave_up_feedback(pool: asyncpg.Pool) -> None:
    gw = FakeGateway()
    svc = GameNightService(pool, gw, S, FakeClock(NOW), retry_sleep=no_sleep)

    async def gave_up(now: datetime) -> list[str]:
        return ["feedback #3 from Ann"]

    await tick(svc, NOW, gave_up)
    logs = [m for m in gw.messages.values() if m.channel_id == 400 and m.embed]
    assert logs and "feedback #3" in logs[-1].embed.description


async def test_tick_lock_isolation(pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> None:
    """One lock_night failure must not skip remaining due nights."""
    gw, clock = FakeGateway(), FakeClock(NOW)
    async with pool.acquire() as c:
        await games.suggest(c, "Game A", None, force=True)
        await games.suggest(c, "Game B", None, force=True)
    svc = GameNightService(pool, gw, S, clock, retry_sleep=no_sleep)

    # Create two nights due at the same time
    n1 = await svc.create_night(1, NOW + timedelta(hours=1))
    n2 = await svc.create_night(1, NOW + timedelta(hours=1))

    # Patch lock_night to fail on the first call, succeed on the second
    call_count = 0

    async def failing_lock_night(night_id: int) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("database error")
        # Delegate to real lock_night for the second call
        return await GameNightService.lock_night(svc, night_id)

    monkeypatch.setattr(svc, "lock_night", failing_lock_night)

    clock.set(NOW + timedelta(hours=1, minutes=1))
    await tick(svc, clock.now())

    # First night should fail, second should still lock
    async with pool.acquire() as c:
        s1 = await c.fetchval("select status from nights where id=$1", n1.id)
        s2 = await c.fetchval("select status from nights where id=$1", n2.id)

    assert s1 == "open"  # failed to lock
    assert s2 == "locked"  # still locked despite first failure
    assert any(m.channel_id == 400 for m in gw.messages.values())  # error logged


async def test_tick_due_locks_failure_doesnt_skip_feedback(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """due_locks() failure must be logged and not skip feedback_retry."""
    gw = FakeGateway()
    svc = GameNightService(pool, gw, S, FakeClock(NOW), retry_sleep=no_sleep)

    feedback_calls: list[datetime] = []

    async def feedback_retry(now: datetime) -> list[str]:
        feedback_calls.append(now)
        return []

    async def failing_due_locks(now: datetime) -> list[Any]:
        raise RuntimeError("database connection lost")

    monkeypatch.setattr(svc, "due_locks", failing_due_locks)

    await tick(svc, NOW, feedback_retry)

    # Both error logged and feedback_retry called (not skipped)
    assert feedback_calls == [NOW]
    logs = [m for m in gw.messages.values() if m.channel_id == 400 and m.embed]
    assert any("lock: RuntimeError" in (m.embed.description or "") for m in logs)
