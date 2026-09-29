"""Offline tests for the Discord wiring: nothing here logs in or touches the network."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import discord
import pytest
from discord import app_commands

from tests.fakes import FakeGateway
from tower_bot.bot import TowerBot
from tower_bot.config import Config
from tower_bot.core.clock import FakeClock
from tower_bot.gamenight import games
from tower_bot.gamenight.cog import ConfirmAdd, GameNightCog
from tower_bot.gamenight.service import GameNightService, NotAllowed, Settings
from tower_bot.gamenight.when import WhenError

GUILD, NIGHT_CH, OTHER_CH, ME = 1, 10, 11, 999


def _cfg() -> Config:
    return Config(
        discord_token="t",
        database_url="postgresql://x",
        db_password=None,
        github_token="g",
        github_repo="o/r",
        feedback_labels=("feedback",),
        guild_id=GUILD,
        night_channel_id=NIGHT_CH,
        library_channel_id=12,
        log_channel_id=13,
        admin_role_id=5,
        tz=ZoneInfo("America/New_York"),
        textfile_path=None,
    )


def _svc() -> Any:
    svc = MagicMock()
    svc.settings = SimpleNamespace(guild_id=GUILD, night_channel_id=NIGHT_CH)
    svc.log_error = AsyncMock()
    return svc


async def _bot_with_cog() -> tuple[TowerBot, GameNightCog, Any]:
    bot = TowerBot(_cfg(), cast(Any, MagicMock()))
    svc = _svc()
    cog = GameNightCog(bot, svc, MagicMock())
    await bot.add_cog(cog)
    return bot, cog, svc


async def test_bot_builds_and_registers_commands() -> None:
    bot, _, _ = await _bot_with_cog()
    names = {c.name for c in bot.tree.get_commands()}
    assert names == {"gamenight", "games", "feedback"}
    gn = bot.tree.get_command("gamenight")
    assert isinstance(gn, app_commands.Group)
    assert {c.name for c in gn.commands} == {"new", "poll", "pick", "move", "cancel", "game"}
    gm = bot.tree.get_command("games")
    assert isinstance(gm, app_commands.Group)
    assert {c.name for c in gm.commands} == {"suggest", "list", "remove"}
    assert bot.intents.guild_reactions and bot.intents.guilds
    assert not bot.intents.message_content and not bot.intents.members
    am = bot.allowed_mentions
    assert am is not None
    assert (am.everyone, am.roles, am.users, am.replied_user) == (False, False, True, False)


def _option(bot: TowerBot, path: str, name: str) -> dict[str, Any]:
    group, _, sub = path.partition(" ")
    cmd = bot.tree.get_command(group)
    assert cmd is not None
    d = cmd.to_dict(bot.tree)
    opts = next(o for o in d["options"] if o["name"] == sub)["options"] if sub else d["options"]
    return cast(dict[str, Any], next(o for o in opts if o["name"] == name))


async def test_option_ranges() -> None:
    bot, _, _ = await _bot_with_cog()
    game = _option(bot, "gamenight game", "number")
    assert (game["min_value"], game["max_value"]) == (1, 4)
    pick = _option(bot, "gamenight pick", "option")
    assert (pick["min_value"], pick["max_value"]) == (1, 5)
    text = _option(bot, "feedback", "text")
    assert (text["min_length"], text["max_length"]) == (1, 2000)
    name = _option(bot, "games suggest", "name")
    assert (name["min_length"], name["max_length"]) == (1, 80)


def _payload(guild: int | None, channel: int, user: int, message: int = 77) -> Any:
    return SimpleNamespace(guild_id=guild, channel_id=channel, user_id=user, message_id=message)


async def test_reaction_filter() -> None:
    bot, cog, _ = await _bot_with_cog()
    bot._connection.user = cast(Any, SimpleNamespace(id=ME))
    poked: list[int] = []
    cog.debouncer = cast(Any, SimpleNamespace(poke=poked.append))
    cog._on_reaction(_payload(GUILD, NIGHT_CH, 5, message=1))  # counts
    cog._on_reaction(_payload(2, NIGHT_CH, 5, message=2))  # other guild
    cog._on_reaction(_payload(None, NIGHT_CH, 5, message=3))  # DM
    cog._on_reaction(_payload(GUILD, OTHER_CH, 5, message=4))  # other channel
    cog._on_reaction(_payload(GUILD, NIGHT_CH, ME, message=5))  # the bot's own reaction
    assert poked == [1]


async def test_reaction_clear_filter() -> None:
    _, cog, _ = await _bot_with_cog()
    poked: list[int] = []
    cog.debouncer = cast(Any, SimpleNamespace(poke=poked.append))

    def ev(guild: int | None, channel: int, message: int) -> Any:
        return SimpleNamespace(guild_id=guild, channel_id=channel, message_id=message, emoji="x")

    for handler in (cog.on_raw_reaction_clear, cog.on_raw_reaction_clear_emoji):
        await handler(ev(GUILD, NIGHT_CH, 1))  # counts
        await handler(ev(2, NIGHT_CH, 2))  # other guild
        await handler(ev(None, NIGHT_CH, 3))  # DM
        await handler(ev(GUILD, OTHER_CH, 4))  # other channel
    assert poked == [1, 1]


def _itx(done: bool) -> Any:
    itx = MagicMock()
    itx.response.is_done.return_value = done
    itx.response.send_message = AsyncMock()
    itx.followup.send = AsyncMock()
    itx.command.qualified_name = "gamenight new"
    return itx


@pytest.mark.parametrize("done", [False, True])
async def test_user_error_reply_before_and_after_defer(done: bool) -> None:
    _, cog, svc = await _bot_with_cog()
    itx = _itx(done)
    err = app_commands.CommandInvokeError(MagicMock(), WhenError("bad time"))
    await cog.cog_app_command_error(itx, err)
    sent = itx.followup.send if done else itx.response.send_message
    other = itx.response.send_message if done else itx.followup.send
    sent.assert_awaited_once_with("bad time", ephemeral=True)
    other.assert_not_awaited()
    svc.log_error.assert_not_awaited()


async def test_unexpected_error_is_logged_and_generic() -> None:
    _, cog, svc = await _bot_with_cog()
    itx = _itx(True)
    err = app_commands.CommandInvokeError(MagicMock(), RuntimeError("db down"))
    await cog.cog_app_command_error(itx, err)
    svc.log_error.assert_awaited_once()
    msg = itx.followup.send.await_args.args[0]
    assert "went wrong" in msg and "db down" not in msg


async def test_error_handler_survives_failures() -> None:
    _, cog, svc = await _bot_with_cog()
    itx = _itx(False)
    itx.response.send_message.side_effect = discord.NotFound(MagicMock(status=404), "gone")
    svc.log_error.side_effect = RuntimeError("log channel down")
    await cog.cog_app_command_error(
        itx, app_commands.CommandInvokeError(MagicMock(), RuntimeError("x"))
    )
    await cog.cog_app_command_error(itx, app_commands.CommandInvokeError(MagicMock(), NotAllowed()))


async def test_scheduler_and_heartbeat_bodies_never_raise(tmp_path: Any) -> None:
    bot = TowerBot(_cfg(), cast(Any, MagicMock()))
    svc = _svc()
    svc.send_due_reminders = AsyncMock(side_effect=RuntimeError("a"))
    svc.due_locks = AsyncMock(side_effect=RuntimeError("b"))
    svc.log_error = AsyncMock(side_effect=RuntimeError("c"))
    bot.service = svc
    bot.feedback = MagicMock(retry_pending=AsyncMock(return_value=[]))
    await bot.scheduler()  # would stop the tasks.loop if it raised
    bot.cfg = cast(Any, SimpleNamespace(textfile_path=tmp_path / "missing" / "x.prom"))
    await bot.heartbeat()


async def test_on_ready_survives_catch_up_failure_and_starts_scheduler_once() -> None:
    bot = TowerBot(_cfg(), cast(Any, MagicMock()))
    svc = _svc()
    svc.catch_up = AsyncMock(side_effect=RuntimeError("discord 503"))
    svc.send_due_reminders = AsyncMock(return_value=0)
    svc.due_locks = AsyncMock(return_value=[])
    bot.service = svc
    bot.feedback = MagicMock(retry_pending=AsyncMock(return_value=[]))
    try:
        await bot.on_ready()
        assert bot.scheduler.is_running()
        await bot.on_ready()  # reconnect: catch-up again, no second start (would raise)
        assert svc.catch_up.await_count == 2
        assert svc.log_error.await_count == 2
    finally:
        bot.scheduler.cancel()
        await asyncio.sleep(0)


async def test_games_suggest_duplicate_then_add_anyway(pool: Any) -> None:
    """/games suggest against the real DB: a near-duplicate name gets the
    ConfirmAdd buttons, only the suggester may press them, and 'Add anyway'
    adds the game and refreshes the library pin."""

    async def no_sleep(_: float) -> None:
        return None

    gw = FakeGateway()
    svc = GameNightService(
        pool,
        gw,
        Settings(GUILD, NIGHT_CH, 12, 13, 5, ZoneInfo("America/New_York")),
        FakeClock(datetime(2026, 9, 28, tzinfo=UTC)),
        retry_sleep=no_sleep,
    )
    bot = TowerBot(_cfg(), pool)
    cog = GameNightCog(bot, svc, MagicMock())
    await bot.add_cog(cog)

    def itx(user_id: int) -> Any:
        i = _itx(False)
        i.user = SimpleNamespace(id=user_id, roles=[])
        i.response.defer = AsyncMock()
        i.response.edit_message = AsyncMock()
        i.followup.send = AsyncMock(return_value=MagicMock())
        return i

    first = itx(42)
    await cog.suggest.callback(cog, first, "Catan")
    first.followup.send.assert_awaited_once_with("Added **Catan**.", ephemeral=True)
    assert len(gw.pinned) == 1  # library pin posted

    second = itx(42)
    await cog.suggest.callback(cog, second, "Catann")
    view = second.followup.send.await_args.kwargs["view"]
    assert isinstance(view, ConfirmAdd)
    assert "Did you mean **Catan**?" in second.followup.send.await_args.args[0]

    stranger = itx(7)
    assert await view.interaction_check(stranger) is False
    stranger.response.send_message.assert_awaited_once_with("Not your suggestion.", ephemeral=True)

    press = itx(42)
    assert await view.interaction_check(press) is True
    await view.add.callback(press)
    press.response.edit_message.assert_awaited_once_with(content="Added **Catann**.", view=None)
    async with svc.connection() as c:
        assert [g.name for g in await games.list_active(c)] == ["Catan", "Catann"]
