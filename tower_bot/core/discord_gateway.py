from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime

import aiohttp
import discord

from tower_bot.core.embed import EmbedSpec
from tower_bot.core.gateway import MessageGone, Snapshot, TransientGatewayError

USERS_ONLY = discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False)


def to_discord_embed(spec: EmbedSpec) -> discord.Embed:
    e = discord.Embed(
        title=spec.title, description=spec.description or None, color=spec.color, url=spec.url
    )
    for f in spec.fields:
        e.add_field(name=f.name, value=f.value, inline=f.inline)
    if spec.footer:
        e.set_footer(text=spec.footer)
    return e


def map_http_error(e: discord.HTTPException) -> Exception:
    if isinstance(e, discord.NotFound):
        return MessageGone(str(e))
    if e.status >= 500:
        return TransientGatewayError(str(e))
    return e


async def _wrap[T](fn: Callable[[], Awaitable[T]]) -> T:
    try:
        return await fn()
    except discord.HTTPException as e:
        mapped = map_http_error(e)
        if mapped is e:
            raise
        raise mapped from e
    except (OSError, TimeoutError, aiohttp.ClientError) as e:
        raise TransientGatewayError(repr(e)) from e


class DiscordGateway:
    """The real Gateway (see tower_bot.core.gateway.Gateway), backed by discord.py REST calls."""

    def __init__(self, client: discord.Client) -> None:
        self._client = client

    def _pm(self, channel_id: int, message_id: int) -> discord.PartialMessage:
        return self._client.get_partial_messageable(channel_id).get_partial_message(message_id)

    async def _guild(self, guild_id: int) -> discord.Guild:
        g = self._client.get_guild(guild_id)
        if g is not None:
            return g
        return await _wrap(lambda: self._client.fetch_guild(guild_id))

    async def post(self, channel_id: int, embed: EmbedSpec, content: str | None = None) -> int:
        ch = self._client.get_partial_messageable(channel_id)
        m = await _wrap(
            lambda: ch.send(
                content=content, embed=to_discord_embed(embed), allowed_mentions=USERS_ONLY
            )
        )
        return m.id

    async def edit(self, channel_id: int, message_id: int, embed: EmbedSpec) -> None:
        pm = self._pm(channel_id, message_id)
        await _wrap(lambda: pm.edit(embed=to_discord_embed(embed)))

    async def add_reactions(self, channel_id: int, message_id: int, emojis: Sequence[str]) -> None:
        pm = self._pm(channel_id, message_id)
        for emoji in emojis:
            await _wrap(lambda emoji=emoji: pm.add_reaction(emoji))  # type: ignore[misc]

    async def reactions(self, channel_id: int, message_id: int) -> Snapshot:
        pm = self._pm(channel_id, message_id)
        me = self._client.user.id if self._client.user else 0

        async def read() -> Snapshot:
            # users() pages over HTTP, so the whole read (fetch + every page) sits inside
            # _wrap: a 5xx or network drop mid-iteration becomes a retryable error.
            msg = await pm.fetch()
            out: Snapshot = {}
            for r in msg.reactions:
                ids = frozenset([u.id async for u in r.users(limit=None)]) - {me}
                out[str(r.emoji)] = ids
            return out

        return await _wrap(read)

    async def reply(self, channel_id: int, message_id: int, content: str) -> int:
        pm = self._pm(channel_id, message_id)
        m = await _wrap(
            lambda: pm.reply(content, mention_author=False, allowed_mentions=USERS_ONLY)
        )
        return m.id

    async def pin(self, channel_id: int, message_id: int) -> None:
        pm = self._pm(channel_id, message_id)
        await _wrap(lambda: pm.pin())

    async def create_event(
        self, guild_id: int, name: str, starts_at: datetime, ends_at: datetime, description: str
    ) -> int:
        g = await self._guild(guild_id)
        ev = await _wrap(
            lambda: g.create_scheduled_event(
                name=name,
                start_time=starts_at,
                end_time=ends_at,  # required for EntityType.external
                description=description,
                entity_type=discord.EntityType.external,
                location="Discord",  # required for EntityType.external
                privacy_level=discord.PrivacyLevel.guild_only,
            )
        )
        return ev.id

    async def edit_event(
        self, guild_id: int, event_id: int, starts_at: datetime, ends_at: datetime, description: str
    ) -> None:
        g = await self._guild(guild_id)
        ev = await _wrap(lambda: g.fetch_scheduled_event(event_id, with_counts=False))
        await _wrap(
            lambda: ev.edit(start_time=starts_at, end_time=ends_at, description=description)
        )

    async def delete_event(self, guild_id: int, event_id: int) -> None:
        g = await self._guild(guild_id)
        ev = await _wrap(lambda: g.fetch_scheduled_event(event_id, with_counts=False))
        await _wrap(lambda: ev.delete())
