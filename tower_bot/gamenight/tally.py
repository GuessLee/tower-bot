from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from tower_bot.gamenight.models import (
    IN,
    MAYBE,
    OUT,
    Candidate,
    PollOption,
    Snapshot,
    norm_emoji,
)


@dataclass(frozen=True)
class Tally:
    going: tuple[int, ...]
    maybe: tuple[int, ...]
    out: tuple[int, ...]
    votes: dict[int, int] = field(default_factory=dict)

    @property
    def total_votes(self) -> int:
        return sum(self.votes.values())


EMPTY_TALLY = Tally((), (), (), {})


def _users(snapshot: Snapshot, emoji: str) -> frozenset[int]:
    want = norm_emoji(emoji)
    found: set[int] = set()
    for k, v in snapshot.items():
        if norm_emoji(k) == want:
            found |= v
    return frozenset(found)


def tally(snapshot: Snapshot, candidates: Sequence[Candidate]) -> Tally:
    going = _users(snapshot, IN)
    maybe = _users(snapshot, MAYBE) - going
    out = _users(snapshot, OUT) - going - maybe
    votes = {c.position: len(_users(snapshot, c.emoji)) for c in candidates}
    return Tally(tuple(sorted(going)), tuple(sorted(maybe)), tuple(sorted(out)), votes)


def choose_winner(t: Tally, candidates: Sequence[Candidate]) -> Candidate | None:
    if not candidates:
        return None

    def key(c: Candidate) -> tuple[int, bool, datetime, int]:
        lp = c.game.last_played
        return (-t.votes.get(c.position, 0), lp is not None, lp or datetime.min, c.position)

    # lp or datetime.min: naive min only compared when lp is None for both, never mixed
    return min(candidates, key=key)


def poll_counts(snapshot: Snapshot, options: Sequence[PollOption]) -> dict[int, int]:
    return {o.position: len(_users(snapshot, o.emoji)) for o in options}
