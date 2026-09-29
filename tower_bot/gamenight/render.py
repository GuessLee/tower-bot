from __future__ import annotations

from collections.abc import Sequence
from zoneinfo import ZoneInfo

from tower_bot.core.embed import EmbedSpec, Field
from tower_bot.gamenight.models import IN, MAYBE, OUT, Candidate, Game, Night, Poll
from tower_bot.gamenight.tally import Tally
from tower_bot.gamenight.when import fmt_when

__all__ = [
    "EmbedSpec",
    "jump_link",
    "mentions",
    "night_card",
    "poll_card",
    "next_up_card",
    "library_card",
]

GREEN, GREY, RED, BLURPLE = 0x57F287, 0x99AAB5, 0xED4245, 0x5865F2

# Discord embed hard limits (title 256, description 4096, field value 1024, 25 fields).
# Renderers below build from mostly-fixed text plus a few bounded inputs (game names
# are capped at 80 chars, NUMBER_EMOJIS caps votable games at 5), so most fields can
# never realistically overflow. The two spots that concatenate genuinely unbounded
# user input (a night's free-text note, and any caller-supplied candidate sequence)
# get an explicit cap so a pathological input degrades gracefully instead of Discord
# rejecting the whole embed.
DESC_LIMIT = 4096
FIELD_LIMIT = 1024


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def jump_link(guild_id: int, channel_id: int, message_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def mentions(ids: Sequence[int], limit: int = 40) -> str:
    if not ids:
        return "-"
    shown = " ".join(f"<@{i}>" for i in ids[:limit])
    return shown + (f" +{len(ids) - limit} more" if len(ids) > limit else "")


def _votes(n: int) -> str:
    return f"{n} vote" if n == 1 else f"{n} votes"


def _game_lines(cands: Sequence[Candidate], t: Tally, chosen: Candidate | None) -> str:
    if not cands:
        return "No games in the library yet. Add one with `/games suggest`."
    lines = []
    for c in cands:
        crown = " 👑" if chosen is not None and chosen.position == c.position else ""
        lines.append(f"{c.emoji} **{c.game.name}** - {_votes(t.votes.get(c.position, 0))}{crown}")
    return "\n".join(lines)


def night_card(
    night: Night, cands: Sequence[Candidate], t: Tally, chosen: Candidate | None, tz: ZoneInfo
) -> EmbedSpec:
    when = fmt_when(night.starts_at, tz)
    if night.status == "cancelled":
        return EmbedSpec(
            title=f"❌ Cancelled - Game Night {when}",
            description="This game night was cancelled.",
            color=RED,
            footer=f"Night #{night.id}",
        )
    note = f"{night.note}\n\n" if night.note else ""
    if night.status == "locked":
        playing = (
            f"🔒 Playing: **{chosen.game.name}**" if chosen else "🔒 Locked, pick a game in chat."
        )
        desc, color = note + playing, GREY
    else:
        desc = note + (
            f"React {IN} in, {MAYBE} maybe, {OUT} out. "
            "Vote for games with the numbers (as many as you like)."
        )
        color = GREEN
    fields = (
        Field(f"{IN} In ({len(t.going)})", mentions(t.going), True),
        Field(f"{MAYBE} Maybe ({len(t.maybe)})", mentions(t.maybe), True),
        Field(f"{OUT} Out ({len(t.out)})", mentions(t.out), True),
        Field("Game vote", _cap(_game_lines(cands, t, chosen), FIELD_LIMIT)),
    )
    return EmbedSpec(
        title=f"🎮 Game Night - {when}",
        description=_cap(desc, DESC_LIMIT),
        fields=fields,
        footer=f"Night #{night.id}",
        color=color,
    )


def poll_card(poll: Poll, counts: dict[int, int], tz: ZoneInfo) -> EmbedSpec:
    lines = [
        f"{o.emoji} {fmt_when(o.starts_at, tz)} - {_votes(counts.get(o.position, 0))}"
        for o in poll.options
    ]
    if poll.status == "open":
        title = "🗳️ When should we play?"
        tail = "\n\nReact with every time that works. The poster locks one with `/gamenight pick`."
        color = BLURPLE
    else:
        title = "🗳️ Poll closed"
        tail = "\n\nA night was created from this poll." if poll.status == "picked" else ""
        color = GREY
    desc = _cap("\n".join(lines) + tail, DESC_LIMIT)
    return EmbedSpec(title=title, description=desc, color=color, footer=f"Poll #{poll.id}")


def next_up_card(
    night: Night | None,
    cands: Sequence[Candidate],
    t: Tally,
    chosen: Candidate | None,
    tz: ZoneInfo,
    link: str | None,
) -> EmbedSpec:
    if night is None:
        return EmbedSpec(
            title="🎮 Next up",
            description="No game night scheduled. Start one with `/gamenight new`.",
            color=GREY,
        )
    game = f"**{chosen.game.name}**" if chosen else "no votes yet"
    desc = (
        f"**{fmt_when(night.starts_at, tz)}**\n"
        f"{len(t.going)} in · {len(t.maybe)} maybe\n"
        f"Leading game: {game}"
    )
    if link:
        desc += f"\n\n[Open the card]({link})"
    return EmbedSpec(
        title="🎮 Next up",
        description=_cap(desc, DESC_LIMIT),
        color=GREEN,
        url=link,
        footer=f"Night #{night.id}",
    )


def library_card(games: Sequence[Game], tz: ZoneInfo) -> EmbedSpec:
    lines: list[str] = []
    used = 0
    for i, g in enumerate(games):
        last = g.last_played.astimezone(tz).strftime("%b %d") if g.last_played else "never"
        line = f"**{g.name}** · played {g.times_played}x · last {last}"
        more = f"\n...and {len(games) - i} more (`/games list`)"
        if used + len(line) + 1 + len(more) > DESC_LIMIT:
            lines.append(more.strip())
            break
        lines.append(line)
        used += len(line) + 1
    desc = "\n".join(lines) if lines else "Empty. Add games with `/games suggest`."
    return EmbedSpec(
        title=f"📚 Game library ({len(games)} games)",
        description=desc,
        color=BLURPLE,
        footer="Suggest with /games suggest",
    )
