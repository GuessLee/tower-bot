from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from tower_bot.gamenight import render
from tower_bot.gamenight.models import Candidate, Game, Night, Poll, PollOption
from tower_bot.gamenight.tally import EMPTY_TALLY, Tally

TZ = ZoneInfo("America/New_York")
START = datetime(2026, 10, 8, 1, 0, tzinfo=UTC)  # Wed Oct 7 9 PM ET
G1 = Game(1, "Fall Guys", None, START, None, 0, True)
G2 = Game(2, "Among Us", None, START, START, 3, True)
CANDS = [Candidate(1, "1️⃣", G1), Candidate(2, "2️⃣", G2)]
NIGHT = Night(
    7, 100, 200, 300, None, START, "bring snacks", "open", 42, None, False, False, False, START
)


def text(e: render.EmbedSpec) -> str:
    return " ".join([e.title, e.description, e.footer] + [f.name + " " + f.value for f in e.fields])


def test_open_night_card() -> None:
    t = Tally((5, 6), (7,), (), {1: 2, 2: 1})
    e = render.night_card(NIGHT, CANDS, t, CANDS[0], TZ)
    s = text(e)
    assert "Wed Oct 7, 9:00 PM ET" in e.title
    assert "bring snacks" in e.description
    assert "<@5> <@6>" in s and "<@7>" in s
    assert "In (2)" in s and "Maybe (1)" in s and "Out (0)" in s
    assert "1️⃣ **Fall Guys** - 2 votes 👑" in s
    assert "2️⃣ **Among Us** - 1 vote" in s
    assert "Night #7" in e.footer


def test_locked_and_cancelled_cards() -> None:
    locked = render.night_card(replace(NIGHT, status="locked"), CANDS, EMPTY_TALLY, CANDS[1], TZ)
    assert "Playing: **Among Us**" in locked.description
    cancelled = render.night_card(replace(NIGHT, status="cancelled"), CANDS, EMPTY_TALLY, None, TZ)
    assert cancelled.title.startswith("❌ Cancelled")


def test_night_card_with_no_games() -> None:
    e = render.night_card(NIGHT, [], EMPTY_TALLY, None, TZ)
    assert "/games suggest" in text(e)


def test_mentions_truncates() -> None:
    assert render.mentions([]) == "-"
    s = render.mentions(list(range(1, 51)), limit=40)
    assert s.endswith("+10 more") and "<@40>" in s and "<@41>" not in s


def test_poll_card() -> None:
    poll = Poll(
        3,
        100,
        200,
        400,
        42,
        "open",
        None,
        (PollOption(1, "1️⃣", START), PollOption(2, "2️⃣", datetime(2026, 10, 9, 1, 0, tzinfo=UTC))),
    )
    e = render.poll_card(poll, {1: 3, 2: 0}, TZ)
    s = text(e)
    assert "1️⃣ Wed Oct 7, 9:00 PM ET - 3 votes" in s
    assert "2️⃣ Thu Oct 8, 9:00 PM ET - 0 votes" in s
    assert "Poll #3" in e.footer
    picked = render.poll_card(replace(poll, status="picked"), {1: 3, 2: 0}, TZ)
    assert "closed" in picked.title.lower()


def test_next_up() -> None:
    none = render.next_up_card(None, [], EMPTY_TALLY, None, TZ, None)
    assert "/gamenight new" in none.description
    e = render.next_up_card(
        NIGHT,
        CANDS,
        Tally((5,), (), (), {1: 1, 2: 0}),
        CANDS[0],
        TZ,
        "https://discord.com/channels/100/200/300",
    )
    s = text(e)
    assert "Wed Oct 7, 9:00 PM ET" in s and "1 in" in s and "Fall Guys" in s
    assert e.url == "https://discord.com/channels/100/200/300"


def test_library_card_truncates() -> None:
    many = [Game(i, f"Game {i:03d}", None, START, None, 0, True) for i in range(300)]
    e = render.library_card(many, TZ)
    assert len(e.description) <= 4096
    assert "more" in e.description and "/games list" in e.description
    assert "300 games" in e.title
    assert "never" in render.library_card([G1], TZ).description


def test_night_card_long_note_capped_to_description_limit() -> None:
    # A Discord modal paragraph input can carry up to 4000 chars; the note alone,
    # plus the fixed instructional text, must never push the embed past Discord's
    # 4096-char description limit.
    huge_note = "n" * 4000
    e = render.night_card(replace(NIGHT, note=huge_note), CANDS, EMPTY_TALLY, None, TZ)
    assert len(e.description) <= 4096
    assert huge_note[:100] in e.description  # still shows the note, just capped overall


def test_night_card_long_game_vote_field_capped_to_field_limit() -> None:
    # Field values are capped at 1024 chars. Even though today's UI limits votes to
    # NUMBER_EMOJIS (5 slots) with 80-char game names, the renderer itself must not
    # assume that and must cap defensively for any Sequence[Candidate] it's given.
    long_cands = [
        Candidate(i, f"#{i}", Game(i, "X" * 80, None, START, None, 0, True)) for i in range(1, 21)
    ]
    t = Tally((), (), (), {i: 0 for i in range(1, 21)})
    e = render.night_card(NIGHT, long_cands, t, None, TZ)
    field = next(f for f in e.fields if f.name.startswith("Game vote"))
    assert len(field.value) <= 1024
