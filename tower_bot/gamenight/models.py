from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

IN, MAYBE, OUT = "✅", "❔", "❌"
RSVP_EMOJIS: tuple[str, ...] = (IN, MAYBE, OUT)
NUMBER_EMOJIS: tuple[str, ...] = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣")
Snapshot = dict[str, frozenset[int]]


def norm_emoji(e: str) -> str:
    """Discord may or may not include U+FE0F; compare without it."""
    return e.replace("️", "")


@dataclass(frozen=True)
class Game:
    id: int
    name: str
    suggested_by: int | None
    created_at: datetime
    last_played: datetime | None
    times_played: int
    active: bool

    @classmethod
    def from_row(cls, r: Any) -> Game:
        return cls(
            r["id"],
            r["name"],
            r["suggested_by"],
            r["created_at"],
            r["last_played"],
            r["times_played"],
            r["active"],
        )


@dataclass(frozen=True)
class Candidate:
    position: int
    emoji: str
    game: Game


@dataclass(frozen=True)
class Night:
    id: int
    guild_id: int
    channel_id: int
    message_id: int | None
    event_id: int | None
    starts_at: datetime
    note: str | None
    status: str
    poster_id: int
    chosen_game_id: int | None
    chosen_override: bool
    reminded_24h: bool
    reminded_1h: bool
    created_at: datetime

    @classmethod
    def from_row(cls, r: Any) -> Night:
        return cls(**{k: r[k] for k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class PollOption:
    position: int
    emoji: str
    starts_at: datetime


@dataclass(frozen=True)
class Poll:
    id: int
    guild_id: int
    channel_id: int
    message_id: int | None
    poster_id: int
    status: str
    night_id: int | None
    options: tuple[PollOption, ...]
