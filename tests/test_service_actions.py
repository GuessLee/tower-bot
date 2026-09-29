from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from tests.fakes import FakeGateway
from tower_bot.core.clock import FakeClock
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
