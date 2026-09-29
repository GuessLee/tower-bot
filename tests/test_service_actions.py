import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from tests.fakes import FakeGateway
from tower_bot.core.clock import FakeClock
from tower_bot.core.gateway import TransientGatewayError
from tower_bot.gamenight import games
from tower_bot.gamenight.service import (
    GameNightService,
    InvalidState,
    NotAllowed,
    NotFound,
    Settings,
)
from tower_bot.gamenight.when import WhenError

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 28, 21, 0, tzinfo=UTC)  # Mon 5 PM ET
S = Settings(
    guild_id=100,
    night_channel_id=200,
    library_channel_id=300,
    log_channel_id=400,
    admin_role_id=9,
    tz=TZ,
)
ADMIN = [9]


async def no_sleep(_: float) -> None:
    return None


@pytest.fixture
async def env(pool: asyncpg.Pool) -> tuple[GameNightService, FakeGateway, FakeClock]:
    gw, clock = FakeGateway(), FakeClock(NOW)
    async with pool.acquire() as c:
        for name in ("Fall Guys", "Among Us", "Rocket League", "Overcooked"):
            await games.suggest(c, name, None, force=True, source="seed")
        await c.execute("update games set created_at = $1", NOW - timedelta(days=30))
    return GameNightService(pool, gw, S, clock, retry_sleep=no_sleep), gw, clock


async def test_permissions(env) -> None:  # type: ignore[no-untyped-def]
    svc, _, _ = env
    n = await svc.create_night(42, NOW + timedelta(days=2))
    with pytest.raises(NotAllowed):
        await svc.cancel_night(7, [], n.id)
    await svc.cancel_night(7, ADMIN, n.id)  # admin may


async def test_resolve_defaults_to_next_open(env) -> None:  # type: ignore[no-untyped-def]
    svc, _, _ = env
    with pytest.raises(NotFound):
        await svc.resolve_night(None)
    later = await svc.create_night(1, NOW + timedelta(days=5))
    sooner = await svc.create_night(1, NOW + timedelta(days=2))
    assert (await svc.resolve_night(None)).id == sooner.id
    assert (await svc.resolve_night(later.id)).id == later.id


async def test_move_updates_event_flags_and_notifies(env) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    n = await svc.create_night(42, NOW + timedelta(days=3))
    moved = await svc.move_night(42, [], NOW + timedelta(hours=5), n.id)
    assert moved.reminded_24h and not moved.reminded_1h
    assert gw.events[n.event_id]["starts_at"] == NOW + timedelta(hours=5)
    assert any("Moved to" in text for _, text in gw.replies)
    with pytest.raises(WhenError):
        await svc.move_night(42, [], NOW - timedelta(hours=1), n.id)


async def test_cancel(env, pool: asyncpg.Pool) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    n = await svc.create_night(42, NOW + timedelta(days=2))
    await svc.cancel_night(42, [], n.id)
    assert n.event_id not in gw.events
    card = gw.messages[n.message_id].embed
    assert card is not None and card.title.startswith("❌ Cancelled")
    with pytest.raises(InvalidState):
        await svc.cancel_night(42, [], n.id)


async def test_lock_picks_winner_and_records_play(env, pool: asyncpg.Pool) -> None:  # type: ignore[no-untyped-def]
    svc, gw, clock = env
    n = await svc.create_night(42, NOW + timedelta(days=2))
    gw.react(n.message_id, "3️⃣", 5)
    gw.react(n.message_id, "3️⃣", 6)
    gw.react(n.message_id, "1️⃣", 5)
    clock.set(n.starts_at)
    due = await svc.due_locks(clock.now())
    assert [d.id for d in due] == [n.id]
    chosen = await svc.lock_night(n.id)
    assert chosen is not None and chosen.position == 3
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "select times_played, last_played from games where id=$1", chosen.game.id
        )
    assert row["times_played"] == 1 and row["last_played"] == n.starts_at
    assert any("Game locked" in t for _, t in gw.replies)
    assert await svc.due_locks(clock.now()) == []
    assert await svc.lock_night(n.id) is None  # idempotent


async def test_override_wins_at_lock(env) -> None:  # type: ignore[no-untyped-def]
    svc, gw, clock = env
    n = await svc.create_night(42, NOW + timedelta(days=2))
    gw.react(n.message_id, "1️⃣", 5)
    picked = await svc.override_game(42, [], 4, n.id)
    assert picked.position == 4
    with pytest.raises(InvalidState):
        await svc.override_game(42, [], 9, n.id)
    clock.set(n.starts_at)
    chosen = await svc.lock_night(n.id)
    assert chosen is not None and chosen.position == 4


async def test_reminders_24h_then_1h_mentions_in_and_maybe(env) -> None:  # type: ignore[no-untyped-def]
    svc, gw, clock = env
    n = await svc.create_night(42, NOW + timedelta(days=2))
    gw.react(n.message_id, "✅", 5)
    gw.react(n.message_id, "❔", 6)
    gw.react(n.message_id, "❌", 7)
    assert await svc.send_due_reminders(clock.now()) == 0
    clock.set(n.starts_at - timedelta(hours=24))
    assert await svc.send_due_reminders(clock.now()) == 1
    _, text = gw.replies[-1]
    assert "tomorrow" in text and "<@5>" in text and "<@6>" in text and "<@7>" not in text
    assert await svc.send_due_reminders(clock.now()) == 0  # not twice
    clock.set(n.starts_at - timedelta(minutes=59))
    assert await svc.send_due_reminders(clock.now()) == 1
    assert "1 hour" in gw.replies[-1][1]


async def test_bot_down_sends_only_1h_reminder(env) -> None:  # type: ignore[no-untyped-def]
    svc, gw, clock = env
    n = await svc.create_night(42, NOW + timedelta(days=2))
    clock.set(n.starts_at - timedelta(minutes=30))
    assert await svc.send_due_reminders(clock.now()) == 1
    assert "1 hour" in gw.replies[-1][1]
    assert await svc.send_due_reminders(clock.now()) == 0


async def test_bot_down_past_start_locks_without_reminding(env) -> None:  # type: ignore[no-untyped-def]
    svc, gw, clock = env
    n = await svc.create_night(42, NOW + timedelta(days=2))
    clock.set(n.starts_at + timedelta(hours=2))
    assert await svc.send_due_reminders(clock.now()) == 0
    assert [d.id for d in await svc.due_locks(clock.now())] == [n.id]


async def test_nobody_in_reminder(env) -> None:  # type: ignore[no-untyped-def]
    svc, gw, clock = env
    n = await svc.create_night(42, NOW + timedelta(days=2))
    clock.set(n.starts_at - timedelta(minutes=30))
    await svc.send_due_reminders(clock.now())
    assert "Nobody's in yet" in gw.replies[-1][1]


async def test_poll_orders_wednesdays_first_and_picks(env, pool: asyncpg.Pool) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    thu = datetime(2026, 10, 2, 1, 0, tzinfo=UTC)  # Thu Oct 1 9 PM ET
    wed = datetime(2026, 10, 8, 1, 0, tzinfo=UTC)  # Wed Oct 7 9 PM ET
    poll = await svc.create_poll(42, [thu, wed])
    assert [o.starts_at for o in poll.options] == [wed, thu]
    assert poll.message_id is not None
    assert list(gw.messages[poll.message_id].reactions) == ["1️⃣", "2️⃣"]
    gw.react(poll.message_id, "2️⃣", 5)
    await svc.refresh_message(poll.message_id)
    assert "1 vote" in gw.messages[poll.message_id].embed.description
    with pytest.raises(NotAllowed):
        await svc.pick_poll(7, [], 2)
    night = await svc.pick_poll(42, [], 2)
    assert night.starts_at == thu
    assert "closed" in gw.messages[poll.message_id].embed.title.lower()
    with pytest.raises(NotFound):
        await svc.pick_poll(42, [], 1)  # no open polls left


@pytest.mark.parametrize("n", [1, 6])
async def test_poll_size_limits(env, n: int) -> None:  # type: ignore[no-untyped-def]
    svc, _, _ = env
    with pytest.raises(WhenError):
        await svc.create_poll(42, [NOW + timedelta(days=i + 1) for i in range(n)])


async def test_poll_rejects_duplicates_and_past(env) -> None:  # type: ignore[no-untyped-def]
    svc, _, _ = env
    t = NOW + timedelta(days=1)
    with pytest.raises(WhenError):
        await svc.create_poll(42, [t, t])
    with pytest.raises(WhenError):
        await svc.create_poll(42, [t, NOW - timedelta(days=1)])


async def test_catch_up_applies_reactions_made_while_down(env) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    n = await svc.create_night(42, NOW + timedelta(days=2))
    gw.react(n.message_id, "✅", 5)  # bot "down": no refresh called
    await svc.catch_up()
    card = gw.messages[n.message_id].embed
    assert card is not None and any(f.name == "✅ In (1)" for f in card.fields)


# --- fix round 1 (review findings) ------------------------------------------


async def test_cancel_survives_card_edit_failure(env, pool: asyncpg.Pool) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    n = await svc.create_night(42, NOW + timedelta(days=2))
    gw.fail_next("edit", times=3)
    await svc.cancel_night(42, [], n.id)  # must not raise: the cancel already committed
    async with pool.acquire() as c:
        status = await c.fetchval("select status from nights where id=$1", n.id)
    assert status == "cancelled"
    assert any(m.channel_id == 400 for m in gw.messages.values())  # #bot-log got a post


async def test_lock_survives_card_edit_failure(env, pool: asyncpg.Pool) -> None:  # type: ignore[no-untyped-def]
    svc, gw, clock = env
    n = await svc.create_night(42, NOW + timedelta(days=2))
    gw.react(n.message_id, "1️⃣", 5)
    clock.set(n.starts_at)
    gw.fail_next("edit", times=3)
    chosen = await svc.lock_night(n.id)  # must not raise: the lock already committed
    assert chosen is not None
    async with pool.acquire() as c:
        status = await c.fetchval("select status from nights where id=$1", n.id)
    assert status == "locked"
    assert any(m.channel_id == 400 for m in gw.messages.values())  # #bot-log got a post


async def test_cancel_survives_delete_event_failure(env, pool: asyncpg.Pool) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    n = await svc.create_night(42, NOW + timedelta(days=2))
    gw.fail_next("delete_event", times=3)
    await svc.cancel_night(42, [], n.id)  # must not raise: the cancel already committed
    async with pool.acquire() as c:
        status = await c.fetchval("select status from nights where id=$1", n.id)
    assert status == "cancelled"
    assert any(m.channel_id == 400 for m in gw.messages.values())  # #bot-log got a post


async def test_lock_skips_if_night_no_longer_open(env, pool: asyncpg.Pool) -> None:  # type: ignore[no-untyped-def]
    svc, gw, clock = env
    n = await svc.create_night(42, NOW + timedelta(days=2))
    gw.react(n.message_id, "1️⃣", 5)
    clock.set(n.starts_at)
    real_reactions = gw.reactions

    async def sneaky_reactions(channel_id: int, message_id: int):  # type: ignore[no-untyped-def]
        # Simulate a concurrent cancel that lands after lock_night has already
        # read reactions but before its own transaction commits.
        async with pool.acquire() as c:
            await c.execute("update nights set status='cancelled' where id=$1", n.id)
        return await real_reactions(channel_id, message_id)

    gw.reactions = sneaky_reactions  # type: ignore[method-assign]

    chosen = await svc.lock_night(n.id)
    assert chosen is None
    async with pool.acquire() as c:
        status = await c.fetchval("select status from nights where id=$1", n.id)
        played = await c.fetchval("select coalesce(sum(times_played), 0) from games")
    assert status == "cancelled"  # the concurrent cancel wins, lock does not overwrite it
    assert played == 0


async def test_lock_skips_if_night_was_moved(env, pool: asyncpg.Pool) -> None:  # type: ignore[no-untyped-def]
    svc, gw, clock = env
    n = await svc.create_night(42, NOW + timedelta(days=2))
    gw.react(n.message_id, "1️⃣", 5)
    clock.set(n.starts_at)
    new_time = n.starts_at + timedelta(days=1)
    real_reactions = gw.reactions

    async def sneaky_reactions(channel_id: int, message_id: int):  # type: ignore[no-untyped-def]
        # Simulate a concurrent move that lands after lock_night has already
        # read reactions but before its own transaction commits. status stays
        # 'open' (move_night does not change it), only starts_at changes.
        async with pool.acquire() as c:
            await c.execute("update nights set starts_at=$2 where id=$1", n.id, new_time)
        return await real_reactions(channel_id, message_id)

    gw.reactions = sneaky_reactions  # type: ignore[method-assign]

    chosen = await svc.lock_night(n.id)
    assert chosen is None
    async with pool.acquire() as c:
        row = await c.fetchrow("select status, starts_at from nights where id=$1", n.id)
        played = await c.fetchval("select coalesce(sum(times_played), 0) from games")
    assert row["status"] == "open"  # the concurrent move wins, lock does not lock it
    assert row["starts_at"] == new_time
    assert played == 0


async def test_reminder_failure_does_not_abort_the_sweep(env, pool: asyncpg.Pool) -> None:  # type: ignore[no-untyped-def]
    svc, gw, clock = env
    n1 = await svc.create_night(42, NOW + timedelta(days=2))
    n2 = await svc.create_night(43, NOW + timedelta(days=2, minutes=5))
    clock.set(n2.starts_at - timedelta(hours=24))  # both nights are due for the 24h reminder
    gw.fail_next("reply", times=3)  # exhausts n1's reminder retries
    assert await svc.send_due_reminders(clock.now()) == 1  # only n2 got through
    async with pool.acquire() as c:
        f1 = await c.fetchrow("select reminded_24h, reminded_1h from nights where id=$1", n1.id)
        f2 = await c.fetchrow("select reminded_24h, reminded_1h from nights where id=$1", n2.id)
    assert not f1["reminded_24h"] and not f1["reminded_1h"]  # retried next tick
    assert f2["reminded_24h"] and not f2["reminded_1h"]
    assert "tomorrow" in gw.replies[-1][1]


async def test_pick_poll_is_race_safe(env, pool: asyncpg.Pool) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    t1 = NOW + timedelta(days=1)
    t2 = NOW + timedelta(days=2)
    poll = await svc.create_poll(42, [t1, t2])
    results = await asyncio.gather(
        svc.pick_poll(42, [], 1, poll.id),
        svc.pick_poll(42, [], 1, poll.id),
        return_exceptions=True,
    )
    errors = [r for r in results if isinstance(r, BaseException)]
    oks = [r for r in results if not isinstance(r, BaseException)]
    assert len(oks) == 1
    assert len(errors) == 1 and isinstance(errors[0], InvalidState)
    async with pool.acquire() as c:
        assert await c.fetchval("select count(*) from nights") == 1


async def test_refresh_poll_does_not_overwrite_picked_status(env, pool: asyncpg.Pool) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    t1 = NOW + timedelta(days=1)
    t2 = NOW + timedelta(days=2)
    poll = await svc.create_poll(42, [t1, t2])
    await svc.pick_poll(42, [], 1, poll.id)
    gw.delete(poll.message_id)
    await svc.refresh_poll(poll.id)
    async with pool.acquire() as c:
        status = await c.fetchval("select status from polls where id=$1", poll.id)
    assert status == "picked"


# --- fix round 2 (review re-check findings) ---------------------------------


async def test_pick_poll_reverts_to_open_if_create_night_fails(env, pool: asyncpg.Pool) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    t1 = NOW + timedelta(days=1)
    t2 = NOW + timedelta(days=2)
    poll = await svc.create_poll(42, [t1, t2])
    gw.fail_next("post", times=3)  # exhausts create_night's card-post retries
    with pytest.raises(TransientGatewayError):
        await svc.pick_poll(42, [], 1, poll.id)
    async with pool.acquire() as c:
        status = await c.fetchval("select status from polls where id=$1", poll.id)
        count = await c.fetchval("select count(*) from nights")
    assert status == "open"  # the claim is reverted, not left stuck 'picked'
    assert count == 0  # create_night's own failure path already deleted the orphan row


async def test_deleted_card_survives_delete_event_failure(env, pool: asyncpg.Pool) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    n = await svc.create_night(42, NOW + timedelta(days=2))
    gw.delete(n.message_id)
    gw.fail_next("delete_event", times=3)
    await svc.refresh_night(n.id)  # must not raise
    async with pool.acquire() as c:
        status = await c.fetchval("select status from nights where id=$1", n.id)
    assert status == "cancelled"
    logs = [
        m.embed.description
        for m in gw.messages.values()
        if m.channel_id == 400 and m.embed is not None
    ]
    assert any("failed after retries" in text for text in logs)  # it was retried and logged
