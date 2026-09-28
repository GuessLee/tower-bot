from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from tests.fakes import FakeGateway
from tower_bot.core.clock import FakeClock
from tower_bot.core.gateway import TransientGatewayError
from tower_bot.gamenight import games
from tower_bot.gamenight.service import GameNightService, Settings
from tower_bot.gamenight.when import WhenError

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 28, 21, 0, tzinfo=UTC)
S = Settings(
    guild_id=100,
    night_channel_id=200,
    library_channel_id=300,
    log_channel_id=400,
    admin_role_id=9,
    tz=TZ,
)


async def no_sleep(_: float) -> None:
    return None


@pytest.fixture
async def env(pool: asyncpg.Pool) -> tuple[GameNightService, FakeGateway, FakeClock]:
    gw, clock = FakeGateway(), FakeClock(NOW)
    async with pool.acquire() as c:
        for name in ("Fall Guys", "Among Us", "Rocket League", "Overcooked", "Fortnite Zero Build"):
            await games.suggest(c, name, None, force=True, source="seed")
        await c.execute("update games set created_at = $1", NOW - timedelta(days=30))
    return GameNightService(pool, gw, S, clock, retry_sleep=no_sleep), gw, clock


def field(gw: FakeGateway, mid: int, prefix: str) -> str:
    e = gw.messages[mid].embed
    assert e is not None
    return next(f.value for f in e.fields if f.name.startswith(prefix))


async def test_create_night_posts_card_reactions_event_and_pins(env) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    night = await svc.create_night(42, NOW + timedelta(days=2), "snacks")
    assert night.message_id is not None and night.event_id is not None
    msg = gw.messages[night.message_id]
    assert msg.channel_id == 200
    assert list(msg.reactions) == ["✅", "❔", "❌", "1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    assert gw.events[night.event_id]["ends_at"] == night.starts_at + timedelta(minutes=90)
    assert "discord.com/channels/100/200/" in str(gw.events[night.event_id]["description"])
    assert not night.reminded_24h and not night.reminded_1h
    # next-up pin posted and pinned
    assert len(gw.pinned) == 1
    pinned = next(iter(gw.pinned))
    assert gw.messages[pinned].embed is not None
    assert "Night #" in gw.messages[pinned].embed.footer


async def test_short_lead_presets_reminder_flags(env) -> None:  # type: ignore[no-untyped-def]
    svc, _, _ = env
    n1 = await svc.create_night(1, NOW + timedelta(hours=3))
    assert n1.reminded_24h and not n1.reminded_1h
    n2 = await svc.create_night(1, NOW + timedelta(minutes=30))
    assert n2.reminded_24h and n2.reminded_1h


async def test_past_time_rejected(env) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    with pytest.raises(WhenError):
        await svc.create_night(1, NOW - timedelta(minutes=1))
    assert gw.posts == []


async def test_refresh_reflects_reactions(env) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    night = await svc.create_night(42, NOW + timedelta(days=2))
    assert night.message_id is not None
    gw.react(night.message_id, "✅", 5)
    gw.react(night.message_id, "2️⃣", 5)
    gw.react(night.message_id, "2️⃣", 6)
    await svc.refresh_message(night.message_id)
    assert "<@5>" in field(gw, night.message_id, "✅ In (1)")
    assert "2 votes 👑" in field(gw, night.message_id, "Game vote")


async def test_deleted_card_marks_cancelled_and_logs(env, pool: asyncpg.Pool) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    night = await svc.create_night(42, NOW + timedelta(days=2))
    assert night.message_id is not None
    gw.delete(night.message_id)
    await svc.refresh_night(night.id)
    async with pool.acquire() as c:
        assert await c.fetchval("select status from nights where id=$1", night.id) == "cancelled"
    assert night.event_id not in gw.events
    log_posts = [m for m in gw.messages.values() if m.channel_id == 400]
    last_embed = log_posts[-1].embed if log_posts else None
    assert last_embed is not None and "deleted" in last_embed.description


async def test_deleted_pin_is_reposted(env) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    await svc.ensure_pins()
    assert len(gw.pinned) == 2
    first = set(gw.pinned)
    for mid in first:
        gw.delete(mid)
    await svc.ensure_pins()
    assert len(gw.pinned) == 2 and gw.pinned.isdisjoint(first)


async def test_library_pin_lists_games(env) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    await svc.refresh_library()
    lib = [m for m in gw.messages.values() if m.channel_id == 300]
    assert len(lib) == 1 and lib[0].embed is not None
    assert "Fall Guys" in lib[0].embed.description and "5 games" in lib[0].embed.title


async def test_edit_failure_retries_then_logs(env) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    night = await svc.create_night(42, NOW + timedelta(days=2))
    gw.fail_next("edit", times=3)
    with pytest.raises(TransientGatewayError):
        await svc.refresh_night(night.id)
    assert any(m.channel_id == 400 for m in gw.messages.values())


async def test_post_failure_leaves_no_orphan_row(env, pool: asyncpg.Pool) -> None:  # type: ignore[no-untyped-def]
    svc, gw, _ = env
    gw.fail_next("post", times=3)
    with pytest.raises(TransientGatewayError):
        await svc.create_night(42, NOW + timedelta(days=2))
    async with pool.acquire() as c:
        assert await c.fetchval("select count(*) from nights") == 0


async def test_empty_library_night(pool: asyncpg.Pool) -> None:
    gw = FakeGateway()
    svc = GameNightService(pool, gw, S, FakeClock(NOW), retry_sleep=no_sleep)
    night = await svc.create_night(1, NOW + timedelta(days=1, hours=1))
    assert night.message_id is not None
    assert list(gw.messages[night.message_id].reactions) == ["✅", "❔", "❌"]
