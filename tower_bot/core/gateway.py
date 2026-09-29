from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from tower_bot.core.embed import EmbedSpec

Snapshot = dict[str, frozenset[int]]


class MessageGone(Exception):
    """Message or event no longer exists (deleted by a human). Never retried."""


class TransientGatewayError(Exception):
    """5xx or network failure talking to Discord. Retried."""


class Gateway(Protocol):
    async def post(self, channel_id: int, embed: EmbedSpec, content: str | None = None) -> int: ...
    async def edit(self, channel_id: int, message_id: int, embed: EmbedSpec) -> None: ...
    async def add_reactions(
        self, channel_id: int, message_id: int, emojis: Sequence[str]
    ) -> None: ...
    async def reactions(self, channel_id: int, message_id: int) -> Snapshot: ...
    async def reply(self, channel_id: int, message_id: int, content: str) -> int: ...
    async def pin(self, channel_id: int, message_id: int) -> None: ...
    async def create_event(
        self, guild_id: int, name: str, starts_at: datetime, ends_at: datetime, description: str
    ) -> int: ...
    async def edit_event(
        self, guild_id: int, event_id: int, starts_at: datetime, ends_at: datetime, description: str
    ) -> None: ...
    async def delete_event(self, guild_id: int, event_id: int) -> None: ...
