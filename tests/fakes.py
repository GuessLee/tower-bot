from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from tower_bot.core.embed import EmbedSpec
from tower_bot.core.gateway import MessageGone, Snapshot, TransientGatewayError


@dataclass
class FakeMessage:
    channel_id: int
    embed: EmbedSpec | None
    content: str | None
    reactions: dict[str, set[int]] = field(default_factory=dict)
    reply_to: int | None = None


class FakeGateway:
    def __init__(self) -> None:
        self.messages: dict[int, FakeMessage] = {}
        self.events: dict[int, dict[str, object]] = {}
        self.pinned: set[int] = set()
        self.replies: list[tuple[int, str]] = []  # (message replied to, content)
        self.posts: list[int] = []
        self._next = 1000
        self._fail: dict[str, int] = {}

    def _id(self) -> int:
        self._next += 1
        return self._next

    def fail_next(self, op: str, times: int = 1) -> None:
        self._fail[op] = times

    def _maybe_fail(self, op: str) -> None:
        if self._fail.get(op, 0) > 0:
            self._fail[op] -= 1
            raise TransientGatewayError(f"fake {op} failure")

    def _msg(self, message_id: int) -> FakeMessage:
        if message_id not in self.messages:
            raise MessageGone()
        return self.messages[message_id]

    # Gateway
    async def post(self, channel_id: int, embed: EmbedSpec, content: str | None = None) -> int:
        self._maybe_fail("post")
        mid = self._id()
        self.messages[mid] = FakeMessage(channel_id, embed, content)
        self.posts.append(mid)
        return mid

    async def edit(self, channel_id: int, message_id: int, embed: EmbedSpec) -> None:
        self._maybe_fail("edit")
        self._msg(message_id).embed = embed

    async def add_reactions(self, channel_id: int, message_id: int, emojis: Sequence[str]) -> None:
        m = self._msg(message_id)
        for e in emojis:
            m.reactions.setdefault(e, set())

    async def reactions(self, channel_id: int, message_id: int) -> Snapshot:
        self._maybe_fail("reactions")
        return {e: frozenset(u) for e, u in self._msg(message_id).reactions.items()}

    async def reply(self, channel_id: int, message_id: int, content: str) -> int:
        self._maybe_fail("reply")
        self._msg(message_id)
        mid = self._id()
        self.messages[mid] = FakeMessage(channel_id, None, content, reply_to=message_id)
        self.replies.append((message_id, content))
        return mid

    async def pin(self, channel_id: int, message_id: int) -> None:
        self._msg(message_id)
        self.pinned.add(message_id)

    async def create_event(
        self, guild_id: int, name: str, starts_at: datetime, ends_at: datetime, description: str
    ) -> int:
        self._maybe_fail("create_event")
        eid = self._id()
        self.events[eid] = {
            "name": name,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "description": description,
        }
        return eid

    async def edit_event(
        self, guild_id: int, event_id: int, starts_at: datetime, ends_at: datetime, description: str
    ) -> None:
        if event_id not in self.events:
            raise MessageGone()
        self.events[event_id].update(starts_at=starts_at, ends_at=ends_at, description=description)

    async def delete_event(self, guild_id: int, event_id: int) -> None:
        if self.events.pop(event_id, None) is None:
            raise MessageGone()

    # test helpers
    def react(self, message_id: int, emoji: str, user_id: int) -> None:
        self.messages[message_id].reactions.setdefault(emoji, set()).add(user_id)

    def unreact(self, message_id: int, emoji: str, user_id: int) -> None:
        self.messages[message_id].reactions.get(emoji, set()).discard(user_id)

    def delete(self, message_id: int) -> None:
        self.messages.pop(message_id, None)
        self.pinned.discard(message_id)
