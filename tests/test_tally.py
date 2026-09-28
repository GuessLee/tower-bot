from datetime import UTC, datetime

from tower_bot.gamenight.models import IN, MAYBE, OUT, Candidate, Game, PollOption
from tower_bot.gamenight.tally import EMPTY_TALLY, choose_winner, poll_counts, tally

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def game(i: int, last: datetime | None) -> Game:
    return Game(i, f"G{i}", None, T0, last, 0, True)


CANDS = [
    Candidate(1, "1️⃣", game(10, datetime(2026, 5, 1, tzinfo=UTC))),
    Candidate(2, "2️⃣", game(20, None)),
    Candidate(3, "3️⃣", game(30, datetime(2026, 3, 1, tzinfo=UTC))),
]


def test_rsvp_strongest_wins_and_sorted() -> None:
    snap = {IN: frozenset({3, 1}), MAYBE: frozenset({1, 2}), OUT: frozenset({1, 2, 4})}
    t = tally(snap, CANDS)
    assert t.going == (1, 3)
    assert t.maybe == (2,)
    assert t.out == (4,)


def test_votes_ignore_variation_selector() -> None:
    snap = {"1⃣": frozenset({1, 2}), "2️⃣": frozenset({1}), "✅": frozenset({9})}
    t = tally(snap, CANDS)
    assert t.votes == {1: 2, 2: 1, 3: 0}
    assert t.total_votes == 3
    assert t.going == (9,)


def test_winner_most_votes() -> None:
    t = tally({"3️⃣": frozenset({1, 2}), "1️⃣": frozenset({1})}, CANDS)
    assert choose_winner(t, CANDS) == CANDS[2]


def test_tie_goes_to_least_recently_played_never_first() -> None:
    t = tally({"1️⃣": frozenset({1}), "2️⃣": frozenset({2}), "3️⃣": frozenset({3})}, CANDS)
    assert choose_winner(t, CANDS) == CANDS[1]  # never played
    t2 = tally({"1️⃣": frozenset({1}), "3️⃣": frozenset({3})}, CANDS)
    assert choose_winner(t2, CANDS) == CANDS[2]  # March beats May


def test_no_votes_and_no_candidates() -> None:
    assert choose_winner(EMPTY_TALLY, CANDS) == CANDS[1]
    assert choose_winner(EMPTY_TALLY, []) is None


def test_poll_counts() -> None:
    opts = [PollOption(1, "1️⃣", T0), PollOption(2, "2️⃣", T0)]
    assert poll_counts({"1️⃣": frozenset({1, 2}), IN: frozenset({5})}, opts) == {1: 2, 2: 0}
