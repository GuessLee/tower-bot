from __future__ import annotations

import logging

import asyncpg
import discord
from discord.ext import commands, tasks

from tower_bot.config import Config
from tower_bot.core.clock import Clock, SystemClock
from tower_bot.core.discord_gateway import USERS_ONLY, DiscordGateway
from tower_bot.core.heartbeat import render_metrics, write_textfile
from tower_bot.gamenight.cog import GameNightCog
from tower_bot.gamenight.feedback import FeedbackService, GitHubIssues
from tower_bot.gamenight.scheduler import tick
from tower_bot.gamenight.service import GameNightService, Settings

log = logging.getLogger(__name__)


class TowerBot(commands.Bot):
    service: GameNightService
    feedback: FeedbackService

    def __init__(self, cfg: Config, pool: asyncpg.Pool, clock: Clock | None = None) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_reactions = True
        # Fail safe for every send, including interaction replies: never @everyone/@here
        # or role pings, and never ping the author of a replied-to message.
        super().__init__(
            command_prefix=commands.when_mentioned, intents=intents, allowed_mentions=USERS_ONLY
        )
        self.cfg, self.pool, self.clock = cfg, pool, clock or SystemClock()
        self._started = False

    async def setup_hook(self) -> None:
        cfg = self.cfg
        self.service = GameNightService(
            self.pool,
            DiscordGateway(self),
            Settings(
                cfg.guild_id,
                cfg.night_channel_id,
                cfg.library_channel_id,
                cfg.log_channel_id,
                cfg.admin_role_id,
                cfg.tz,
            ),
            self.clock,
        )
        self.feedback = FeedbackService(
            self.pool, GitHubIssues(cfg.github_token, cfg.github_repo), cfg.feedback_labels
        )
        await self.add_cog(GameNightCog(self, self.service, self.feedback))
        guild = discord.Object(id=cfg.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.heartbeat.start()

    async def on_ready(self) -> None:
        # on_ready fires again after every reconnect that needed a new session.
        log.info("ready as %s", self.user)
        if not self._started:
            self._started = True
            self.scheduler.start()
        try:
            # Re-read reactions we may have missed while disconnected (or down).
            await self.service.catch_up()
        except Exception as e:
            log.exception("catch-up failed")
            try:
                await self.service.log_error(f"catch-up: {e!r}")
            except Exception:
                log.exception("could not log catch-up failure")

    async def close(self) -> None:
        self.scheduler.cancel()
        self.heartbeat.cancel()
        await super().close()

    # A tasks.loop stops for good on an unhandled exception, so both bodies catch
    # everything: one bad pass must never end reminders, locks or the heartbeat.
    @tasks.loop(seconds=60)
    async def scheduler(self) -> None:
        try:
            await tick(self.service, self.clock.now(), self.feedback.retry_pending)
        except Exception:
            log.exception("scheduler tick failed")

    @tasks.loop(seconds=60)
    async def heartbeat(self) -> None:
        if self.cfg.textfile_path is None:
            return
        try:
            write_textfile(
                self.cfg.textfile_path,
                render_metrics(
                    self.is_ready() and not self.is_closed(), self.latency, self.clock.now()
                ),
            )
        except Exception:
            log.exception("heartbeat write failed")
