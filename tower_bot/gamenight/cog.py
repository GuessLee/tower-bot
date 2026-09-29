from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from tower_bot.core.debounce import Debouncer
from tower_bot.core.gateway import TransientGatewayError
from tower_bot.gamenight import games
from tower_bot.gamenight.feedback import FeedbackService
from tower_bot.gamenight.games import Added, Exists, PossibleDuplicate
from tower_bot.gamenight.service import GameNightService, InvalidState, NotAllowed, NotFound
from tower_bot.gamenight.when import WhenError, fmt_when, parse_many, parse_when

if TYPE_CHECKING:
    from tower_bot.bot import TowerBot

log = logging.getLogger(__name__)
USER_ERRORS = (WhenError, NotAllowed, NotFound, InvalidState, ValueError)
GENERIC_ERROR = "Something went wrong. It's been logged."


def _roles(user: discord.User | discord.Member) -> list[int]:
    return [r.id for r in getattr(user, "roles", [])]


async def _refresh_library(svc: GameNightService) -> None:
    """Best-effort library pin refresh after the DB change already happened: a
    Discord hiccup here must not turn a successful add/remove into an error reply."""
    try:
        await svc.refresh_library()
    except TransientGatewayError:
        pass  # already posted to #bot-log by the service's retry wrapper
    except Exception as e:
        log.exception("library refresh failed")
        try:
            await svc.log_error(f"library refresh: {e!r}")
        except Exception:
            log.exception("could not log library refresh failure")


class ConfirmAdd(discord.ui.View):
    """Buttons shown when a suggested name looks like a game already in the library."""

    def __init__(self, cog: GameNightCog, name: str, user_id: int) -> None:
        super().__init__(timeout=120)
        self.cog, self.name, self.user_id = cog, name, user_id
        self.message: discord.WebhookMessage | None = None

    async def interaction_check(self, itx: discord.Interaction[Any]) -> bool:
        if itx.user.id != self.user_id:
            await itx.response.send_message("Not your suggestion.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Add anyway", style=discord.ButtonStyle.primary)
    async def add(
        self, itx: discord.Interaction[TowerBot], _: discord.ui.Button[ConfirmAdd]
    ) -> None:
        self.stop()
        svc = self.cog.svc
        async with svc.connection() as c:
            r = await games.suggest(c, self.name, self.user_id, force=True)
        if isinstance(r, Added):
            await itx.response.edit_message(content=f"Added **{r.game.name}**.", view=None)
            await _refresh_library(svc)
        else:
            await itx.response.edit_message(
                content=f"**{r.game.name}** is already in the library.", view=None
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, itx: discord.Interaction[TowerBot], _: discord.ui.Button[ConfirmAdd]
    ) -> None:
        self.stop()
        await itx.response.edit_message(content="Not added.", view=None)

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(content="Timed out, not added.", view=None)
            except discord.HTTPException:
                pass  # ephemeral message already gone; nothing to tidy

    async def on_error(
        self,
        itx: discord.Interaction[Any],
        error: Exception,
        item: discord.ui.Item[ConfirmAdd],
    ) -> None:
        await self.cog.report_error(itx, error, "/games suggest (Add anyway)")


class GameNightCog(commands.Cog):
    gamenight = app_commands.Group(name="gamenight", description="Plan game nights")
    games_group = app_commands.Group(name="games", description="The game library")

    def __init__(self, bot: TowerBot, svc: GameNightService, fb: FeedbackService) -> None:
        self.bot, self.svc, self.fb = bot, svc, fb
        self.debouncer = Debouncer(2.0, svc.refresh_message)

    async def cog_unload(self) -> None:
        await self.debouncer.drain()

    # --- errors -----------------------------------------------------------
    async def report_error(
        self, itx: discord.Interaction[Any], err: BaseException, where: str
    ) -> None:
        """Reply to the user with a friendly message. User mistakes (bad time,
        not allowed, ...) are shown as-is; anything else is logged to #bot-log
        and the user gets a generic message. Works both before and after the
        interaction was deferred, and never raises."""
        if isinstance(err, USER_ERRORS):
            msg = str(err) or "That didn't work."
        else:
            log.error("%s failed", where, exc_info=err)
            try:
                await self.svc.log_error(f"{where}: {err!r}")
            except Exception:
                log.exception("could not log command failure")
            msg = GENERIC_ERROR
        try:
            if itx.response.is_done():
                await itx.followup.send(msg, ephemeral=True)
            else:
                await itx.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            log.warning("could not send error reply for %s", where, exc_info=True)

    async def cog_app_command_error(
        self, interaction: discord.Interaction[Any], error: app_commands.AppCommandError
    ) -> None:
        err = getattr(error, "original", error)
        name = interaction.command.qualified_name if interaction.command else "?"
        await self.report_error(interaction, err, f"/{name}")

    # --- /gamenight -----------------------------------------------------
    @gamenight.command(
        name="new", description="Post a game night (default: next Wednesday 9 PM ET)"
    )
    @app_commands.describe(
        when="e.g. 'fri 8pm', '10/14 9pm'. Empty = next Wednesday 9 PM ET",
        note="Optional note shown on the card",
    )
    async def new(
        self, itx: discord.Interaction[TowerBot], when: str | None = None, note: str | None = None
    ) -> None:
        starts = parse_when(when, self.svc.clock.now(), self.svc.settings.tz)
        await itx.response.defer(ephemeral=True, thinking=True)
        night = await self.svc.create_night(itx.user.id, starts, note)
        await itx.followup.send(
            f"Posted game night #{night.id} for **{fmt_when(starts, self.svc.settings.tz)}** "
            f"in <#{self.svc.settings.night_channel_id}>.",
            ephemeral=True,
        )

    @gamenight.command(name="poll", description="Post a time poll (2-5 times)")
    @app_commands.describe(times="Separate with commas, e.g. 'wed 9pm, thu 9pm, sat 8pm'")
    async def poll(self, itx: discord.Interaction[TowerBot], times: str) -> None:
        parsed = parse_many(times, self.svc.clock.now(), self.svc.settings.tz)
        await itx.response.defer(ephemeral=True, thinking=True)
        p = await self.svc.create_poll(itx.user.id, parsed)
        await itx.followup.send(f"Poll #{p.id} posted.", ephemeral=True)

    @gamenight.command(name="pick", description="Lock a poll option into a game night")
    @app_commands.describe(
        option="Option number on the poll", poll_id="Defaults to latest open poll"
    )
    async def pick(
        self,
        itx: discord.Interaction[TowerBot],
        option: app_commands.Range[int, 1, 5],
        poll_id: int | None = None,
    ) -> None:
        await itx.response.defer(ephemeral=True, thinking=True)
        night = await self.svc.pick_poll(itx.user.id, _roles(itx.user), option, poll_id)
        await itx.followup.send(f"Game night #{night.id} created.", ephemeral=True)

    @gamenight.command(name="move", description="Move a game night")
    @app_commands.describe(
        when="e.g. 'fri 8pm', '10/14 9pm'", night_id="Defaults to the next game night"
    )
    async def move(
        self, itx: discord.Interaction[TowerBot], when: str, night_id: int | None = None
    ) -> None:
        starts = parse_when(when, self.svc.clock.now(), self.svc.settings.tz)
        await itx.response.defer(ephemeral=True, thinking=True)
        night = await self.svc.move_night(itx.user.id, _roles(itx.user), starts, night_id)
        await itx.followup.send(f"Night #{night.id} moved.", ephemeral=True)

    @gamenight.command(name="cancel", description="Cancel a game night")
    @app_commands.describe(night_id="Defaults to the next game night")
    async def cancel(self, itx: discord.Interaction[TowerBot], night_id: int | None = None) -> None:
        await itx.response.defer(ephemeral=True, thinking=True)
        night = await self.svc.cancel_night(itx.user.id, _roles(itx.user), night_id)
        await itx.followup.send(f"Night #{night.id} cancelled.", ephemeral=True)

    @gamenight.command(name="game", description="Override the game for a night")
    @app_commands.describe(
        number="Candidate number on the card (1-4)", night_id="Defaults to the next game night"
    )
    async def game(
        self,
        itx: discord.Interaction[TowerBot],
        number: app_commands.Range[int, 1, 4],
        night_id: int | None = None,
    ) -> None:
        await itx.response.defer(ephemeral=True, thinking=True)
        pick = await self.svc.override_game(itx.user.id, _roles(itx.user), number, night_id)
        await itx.followup.send(f"Game set to **{pick.game.name}**.", ephemeral=True)

    # --- /games ---------------------------------------------------------
    @games_group.command(name="suggest", description="Add a game to the library")
    @app_commands.describe(name="The game's name")
    async def suggest(
        self, itx: discord.Interaction[TowerBot], name: app_commands.Range[str, 1, 80]
    ) -> None:
        await itx.response.defer(ephemeral=True, thinking=True)
        async with self.svc.connection() as c:
            r = await games.suggest(c, name, itx.user.id)
        if isinstance(r, Added):
            await itx.followup.send(f"Added **{r.game.name}**.", ephemeral=True)
            await _refresh_library(self.svc)
        elif isinstance(r, Exists):
            await itx.followup.send(f"**{r.game.name}** is already in the library.", ephemeral=True)
        elif isinstance(r, PossibleDuplicate):
            view = ConfirmAdd(self, name, itx.user.id)
            view.message = await itx.followup.send(
                f"Did you mean **{r.game.name}**? It's already in the library.",
                view=view,
                ephemeral=True,
                wait=True,
            )

    @games_group.command(name="list", description="Show the game library")
    async def list_(self, itx: discord.Interaction[TowerBot]) -> None:
        await itx.response.defer(ephemeral=True, thinking=True)
        async with self.svc.connection() as c:
            lib = await games.list_active(c)
        text = "\n".join(f"- {g.name} (played {g.times_played}x)" for g in lib) or "Empty."
        await itx.followup.send(text[:1990], ephemeral=True)

    @games_group.command(name="remove", description="Admin: remove a game from the library")
    @app_commands.describe(name="The game's name")
    async def remove(self, itx: discord.Interaction[TowerBot], name: str) -> None:
        if not self.svc.is_admin(_roles(itx.user)):
            raise NotAllowed("Only admins can remove games.")
        await itx.response.defer(ephemeral=True, thinking=True)
        async with self.svc.connection() as c:
            g = await games.remove(c, name)
        if g is None:
            raise NotFound(f"No active game called {name}.")
        await itx.followup.send(f"Removed **{g.name}**.", ephemeral=True)
        await _refresh_library(self.svc)

    # --- /feedback ------------------------------------------------------
    @app_commands.command(name="feedback", description="Send feedback or report a problem")
    @app_commands.describe(text="What's on your mind (up to 2000 characters)")
    async def feedback(
        self, itx: discord.Interaction[TowerBot], text: app_commands.Range[str, 1, 2000]
    ) -> None:
        await itx.response.defer(ephemeral=True, thinking=True)
        channel = getattr(itx.channel, "name", None) or "dm"
        r = await self.fb.submit(text, itx.user.display_name, channel, self.svc.clock.now())
        await itx.followup.send(
            f"Thanks! Filed: {r.url}"
            if r.url
            else "Thanks! GitHub is down, it's queued and will be filed automatically.",
            ephemeral=True,
        )

    # --- reactions ------------------------------------------------------
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, p: discord.RawReactionActionEvent) -> None:
        self._on_reaction(p)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, p: discord.RawReactionActionEvent) -> None:
        self._on_reaction(p)

    @commands.Cog.listener()
    async def on_raw_reaction_clear(self, p: discord.RawReactionClearEvent) -> None:
        self._on_clear(p)

    @commands.Cog.listener()
    async def on_raw_reaction_clear_emoji(self, p: discord.RawReactionClearEmojiEvent) -> None:
        self._on_clear(p)

    def _watched(self, guild_id: int | None, channel_id: int) -> bool:
        s = self.svc.settings
        return guild_id == s.guild_id and channel_id == s.night_channel_id

    def _on_reaction(self, p: discord.RawReactionActionEvent) -> None:
        if self.bot.user is not None and p.user_id == self.bot.user.id:
            return
        if self._watched(p.guild_id, p.channel_id):
            self.debouncer.poke(p.message_id)

    def _on_clear(
        self, p: discord.RawReactionClearEvent | discord.RawReactionClearEmojiEvent
    ) -> None:
        # A moderator wiping reactions changes the tally too; no user to filter here.
        if self._watched(p.guild_id, p.channel_id):
            self.debouncer.poke(p.message_id)
