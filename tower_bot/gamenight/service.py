from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import TypeVar, cast
from zoneinfo import ZoneInfo

import asyncpg

from tower_bot.core.audit import audit
from tower_bot.core.clock import Clock
from tower_bot.core.embed import EmbedSpec
from tower_bot.core.gateway import Gateway, MessageGone, TransientGatewayError
from tower_bot.core.retry import with_retry
from tower_bot.gamenight import games, render, repo
from tower_bot.gamenight.models import NUMBER_EMOJIS, RSVP_EMOJIS, Candidate, Night
from tower_bot.gamenight.tally import EMPTY_TALLY, Tally, choose_winner, tally
from tower_bot.gamenight.when import WhenError

log = logging.getLogger(__name__)
T = TypeVar("T")
REMIND_24H = timedelta(hours=24)
REMIND_1H = timedelta(hours=1)


@dataclass(frozen=True)
class Settings:
    guild_id: int
    night_channel_id: int
    library_channel_id: int
    log_channel_id: int
    admin_role_id: int
    tz: ZoneInfo
    duration: timedelta = timedelta(minutes=90)
    candidates: int = 4


class NotAllowed(Exception):
    pass


class NotFound(Exception):
    pass


class InvalidState(Exception):
    pass


class GameNightService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        gw: Gateway,
        settings: Settings,
        clock: Clock,
        *,
        retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.pool = pool
        self.gw = gw
        self.settings = settings
        self.clock = clock
        self._sleep = retry_sleep

    # --- permissions -------------------------------------------------------
    def is_admin(self, role_ids: Sequence[int]) -> bool:
        return self.settings.admin_role_id in role_ids

    def can_manage(self, actor_id: int, role_ids: Sequence[int], poster_id: int) -> bool:
        return actor_id == poster_id or self.is_admin(role_ids)

    def _require(self, actor_id: int, role_ids: Sequence[int], poster_id: int) -> None:
        if not self.can_manage(actor_id, role_ids, poster_id):
            raise NotAllowed("Only the poster or an admin can do that.")

    # --- plumbing ----------------------------------------------------------
    def _acquire(self) -> AbstractAsyncContextManager[asyncpg.Connection]:
        # asyncpg-stubs types Pool.acquire() as yielding PoolConnectionProxy, which
        # proxies every Connection method at runtime but isn't declared as a subtype.
        # repo.py functions take asyncpg.Connection, so we cast at the one call site.
        return cast(AbstractAsyncContextManager[asyncpg.Connection], self.pool.acquire())

    async def _call(self, what: str, fn: Callable[[], Awaitable[T]]) -> T:
        try:
            return await with_retry(fn, sleep=self._sleep)
        except TransientGatewayError as e:
            await self.log_error(f"{what} failed after retries: {e}")
            raise

    async def log_error(self, text: str) -> None:
        log.error(text)
        try:
            await self.gw.post(
                self.settings.log_channel_id,
                EmbedSpec(title="tower-bot", description=text[:4000], color=0xED4245),
            )
        except Exception:
            log.exception("could not post to #bot-log")
        async with self._acquire() as c:
            await audit(c, None, "error", None, text=text[:4000])

    def link(self, message_id: int) -> str:
        return render.jump_link(self.settings.guild_id, self.settings.night_channel_id, message_id)

    @staticmethod
    def _flags(starts_at: datetime, now: datetime) -> dict[str, bool]:
        lead = starts_at - now
        return {"reminded_24h": lead <= REMIND_24H, "reminded_1h": lead <= REMIND_1H}

    @staticmethod
    def _chosen(night: Night, cands: Sequence[Candidate], t: Tally) -> Candidate | None:
        if night.chosen_game_id is not None:
            return next((x for x in cands if x.game.id == night.chosen_game_id), None)
        if night.status == "open" and t.total_votes > 0:
            return choose_winner(t, cands)
        return None

    async def _load(self, night_id: int) -> tuple[Night, list[Candidate]]:
        async with self._acquire() as c:
            night = await repo.get_night(c, night_id)
            if night is None:
                raise NotFound(f"No game night #{night_id}.")
            return night, await repo.candidates(c, night_id)

    # --- nights ------------------------------------------------------------
    async def create_night(
        self, poster_id: int, starts_at: datetime, note: str | None = None
    ) -> Night:
        s, now = self.settings, self.clock.now()
        if starts_at <= now:
            raise WhenError("That time is already past.")
        note = (note or "").strip()[:300] or None
        async with self._acquire() as c, c.transaction():
            picked = await games.pick_candidates(c, now, s.candidates)
            night = await repo.insert_night(
                c,
                guild_id=s.guild_id,
                channel_id=s.night_channel_id,
                starts_at=starts_at,
                note=note,
                poster_id=poster_id,
                **self._flags(starts_at, now),
            )
            cands = [Candidate(i + 1, NUMBER_EMOJIS[i], g) for i, g in enumerate(picked)]
            await repo.insert_candidates(c, night.id, cands)
        card = render.night_card(night, cands, EMPTY_TALLY, None, s.tz)
        try:
            msg = await self._call(
                "post night card", lambda: self.gw.post(s.night_channel_id, card)
            )
        except Exception:
            async with self._acquire() as c:
                await repo.delete_night(c, night.id)
            raise
        async with self._acquire() as c:
            night = await repo.update_night(c, night.id, message_id=msg)
        try:
            await self._call(
                "add reactions",
                lambda: self.gw.add_reactions(
                    s.night_channel_id, msg, [*RSVP_EMOJIS, *(x.emoji for x in cands)]
                ),
            )
        except TransientGatewayError:
            pass  # already logged; the card still works without reactions
        try:
            desc = f"{note}\n{self.link(msg)}" if note else self.link(msg)
            event_id = await self._call(
                "create event",
                lambda: self.gw.create_event(
                    s.guild_id, "🎮 Game Night", starts_at, starts_at + s.duration, desc
                ),
            )
            async with self._acquire() as c:
                night = await repo.update_night(c, night.id, event_id=event_id)
        except TransientGatewayError:
            pass  # already logged; the card still works without an event
        async with self._acquire() as c:
            await audit(
                c, poster_id, "night_create", f"night:{night.id}", starts_at=starts_at.isoformat()
            )
        await self.refresh_next_up()
        return night

    async def _mark_card_deleted(self, night: Night) -> None:
        """Cancel a night whose Discord card is gone. Does not refresh any pin;
        callers that need one refreshed call refresh_next_up() themselves, once,
        after this returns (see _card_deleted and refresh_next_up's own loop)."""
        async with self._acquire() as c:
            await repo.update_night(c, night.id, status="cancelled")
            await audit(c, None, "night_card_deleted", f"night:{night.id}")
        if night.event_id is not None:
            try:
                await self.gw.delete_event(self.settings.guild_id, night.event_id)
            except (MessageGone, TransientGatewayError):
                pass
        await self.log_error(f"Night #{night.id} card was deleted in Discord; marked cancelled.")

    async def _card_deleted(self, night: Night) -> None:
        await self._mark_card_deleted(night)
        await self.refresh_next_up()

    async def refresh_night(self, night_id: int) -> None:
        night, cands = await self._load(night_id)
        if night.message_id is None:
            return
        mid = night.message_id
        try:
            snap = await self._call(
                "read reactions", lambda: self.gw.reactions(night.channel_id, mid)
            )
            t = tally(snap, cands)
            card = render.night_card(
                night, cands, t, self._chosen(night, cands, t), self.settings.tz
            )
            await self._call("edit night card", lambda: self.gw.edit(night.channel_id, mid, card))
        except MessageGone:
            if night.status != "cancelled":
                await self._card_deleted(night)
            return
        if night.status == "open":
            await self.refresh_next_up()

    async def refresh_message(self, message_id: int) -> None:
        async with self._acquire() as c:
            night = await repo.night_by_message(c, message_id)
            poll = None if night else await repo.poll_by_message(c, message_id)
        if night is not None:
            await self.refresh_night(night.id)
        elif poll is not None:
            await self.refresh_poll(poll.id)

    # --- pins --------------------------------------------------------------
    async def _upsert_pin(self, kind: str, channel_id: int, embed: EmbedSpec) -> None:
        async with self._acquire() as c:
            pin = await repo.get_pin(c, kind)
        if pin is not None:
            try:
                await self._call(f"edit {kind} pin", lambda: self.gw.edit(pin[0], pin[1], embed))
                return
            except MessageGone:
                pass
        mid = await self._call(f"post {kind} pin", lambda: self.gw.post(channel_id, embed))
        await self._call(f"pin {kind}", lambda: self.gw.pin(channel_id, mid))
        async with self._acquire() as c:
            await repo.upsert_pin(c, kind, channel_id, mid)

    async def refresh_next_up(self) -> None:
        # Loop instead of recursing through _card_deleted: each deleted card we find
        # is cancelled in place and we move on to the next candidate night, so this
        # stays one stack frame regardless of how many cards were deleted.
        while True:
            async with self._acquire() as c:
                night = await repo.next_open(c, self.clock.now())
                cands = await repo.candidates(c, night.id) if night else []
            t, chosen, link = EMPTY_TALLY, None, None
            if night is not None and night.message_id is not None:
                mid = night.message_id
                try:
                    snap = await self._call(
                        "read reactions", partial(self.gw.reactions, night.channel_id, mid)
                    )
                except MessageGone:
                    await self._mark_card_deleted(night)
                    continue
                t = tally(snap, cands)
                chosen = self._chosen(night, cands, t)
                link = self.link(mid)
            card = render.next_up_card(night, cands, t, chosen, self.settings.tz, link)
            await self._upsert_pin("next_up", self.settings.night_channel_id, card)
            return

    async def refresh_library(self) -> None:
        async with self._acquire() as c:
            lib = await games.list_active(c)
        await self._upsert_pin(
            "library", self.settings.library_channel_id, render.library_card(lib, self.settings.tz)
        )

    async def ensure_pins(self) -> None:
        await self.refresh_next_up()
        await self.refresh_library()

    # --- polls (implemented in Task 10) --------------------------------------
    async def refresh_poll(self, poll_id: int) -> None:
        raise NotImplementedError
