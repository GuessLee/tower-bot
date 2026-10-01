"""Render the how-to GIFs under docs/demo/.

Each scene drives the real cog callbacks and GameNightService against a scratch
Postgres database and the tests' FakeGateway (no Discord connection, no tokens),
snapshots the fake channel after every step, and replays those snapshots through
chat.html in headless Chromium. So every card, reply and tally in the GIFs is what
the bot really produces; only the Discord chrome around them is drawn by hand.

    pip install -e ".[dev,demo]" && playwright install chromium
    python -m scripts.demo.make_demo             # all scenes
    python -m scripts.demo.make_demo --only 03   # one scene

Needs the same Postgres as the tests (TEST_DATABASE_URL). Re-run after changing
anything in render.py or the command replies so the GIFs do not go stale.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import asyncpg

from tests.fakes import FakeGateway
from tower_bot.bot import TowerBot
from tower_bot.config import Config
from tower_bot.core.clock import FakeClock
from tower_bot.core.db import apply_migrations, create_pool
from tower_bot.core.embed import EmbedSpec
from tower_bot.gamenight.cog import GameNightCog
from tower_bot.gamenight.games import normalize
from tower_bot.gamenight.models import IN, MAYBE, OUT
from tower_bot.gamenight.scheduler import tick
from tower_bot.gamenight.service import GameNightService, Settings
from tower_bot.gamenight.when import default_start

HERE = Path(__file__).resolve().parent
CHAT_URI = (HERE / "chat.html").as_uri()
OUT_DIR = HERE.parents[1] / "docs" / "demo"
WIDTH, HEIGHT = 760, 920

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 27, 19, 30, tzinfo=TZ)  # a Sunday evening
GUILD, NIGHT_CH, LIB_CH, LOG_CH, ADMIN_ROLE = 1, 10, 12, 13, 5
CHANNELS = {NIGHT_CH: "game-night", LIB_CH: "game-library"}
TOPICS = {NIGHT_CH: "Game nights, polls and RSVPs", LIB_CH: "Everything in the rotation"}
TABLES = "games, nights, night_candidates, polls, poll_options, pins, audit_log, feedback_outbox"

# Made-up people. "You" is whoever is watching the GIF.
YOU, DRE, MAYA, KOFI, JULES = 101, 102, 103, 104, 105
USERS = {
    YOU: {"name": "You", "color": "#5865f2", "glyph": "Y"},
    DRE: {"name": "Dre", "color": "#3ba55d", "glyph": "D"},
    MAYA: {"name": "Maya", "color": "#eb459e", "glyph": "M"},
    KOFI: {"name": "Kofi", "color": "#f0b232", "glyph": "K"},
    JULES: {"name": "Jules", "color": "#e67e22", "glyph": "J"},
}
BOT = {"name": "tower-bot", "color": "#f0a020", "glyph": "🎮"}

# (name, times played, last played) so the library and the vote rotation look lived-in.
SEED_GAMES: list[tuple[str, int, datetime | None]] = [
    ("Rocket League", 5, datetime(2026, 9, 23, 21, tzinfo=TZ)),
    ("Among Us", 3, datetime(2026, 9, 16, 21, tzinfo=TZ)),
    ("Fall Guys", 2, datetime(2026, 9, 9, 21, tzinfo=TZ)),
    ("Pummel Party", 2, datetime(2026, 9, 2, 21, tzinfo=TZ)),
    ("Jackbox Party Pack", 4, datetime(2026, 8, 26, 21, tzinfo=TZ)),
    ("Golf With Your Friends", 1, datetime(2026, 8, 12, 21, tzinfo=TZ)),
    ("Overcooked 2", 0, None),
    ("Deep Rock Galactic", 0, None),
]

# Slash command -> attribute holding it on the cog.
CALLBACKS = {
    "gamenight new": "new",
    "gamenight poll": "poll",
    "gamenight pick": "pick",
    "gamenight move": "move",
    "gamenight cancel": "cancel",
    "gamenight game": "game",
    "games suggest": "suggest",
    "games list": "list_",
    "games remove": "remove",
}
NIGHT_CARD, POLL_CARD = "🎮 Game Night", "🗳️"


async def _no_sleep(_: float) -> None:
    return None


class DemoGateway(FakeGateway):
    """The tests' fake Discord, plus what the picture needs: when each message
    was posted and whether it has been edited since."""

    def __init__(self, clock: FakeClock) -> None:
        super().__init__()
        self.clock = clock
        self.posted_at: dict[int, datetime] = {}
        self.edited: set[int] = set()

    async def post(self, channel_id: int, embed: EmbedSpec, content: str | None = None) -> int:
        mid = await super().post(channel_id, embed, content)
        self.posted_at[mid] = self.clock.now()
        return mid

    async def reply(self, channel_id: int, message_id: int, content: str) -> int:
        mid = await super().reply(channel_id, message_id, content)
        self.posted_at[mid] = self.clock.now()
        return mid

    async def edit(self, channel_id: int, message_id: int, embed: EmbedSpec) -> None:
        await super().edit(channel_id, message_id, embed)
        self.edited.add(message_id)


@dataclass
class Ephemeral:
    """An 'Only you can see this' reply to a slash command."""

    user: int
    command: str
    channel: int
    at: datetime
    content: str = ""
    buttons: list[tuple[str, str]] = field(default_factory=list)
    view: Any = None


class Interaction:
    """The slice of discord.Interaction the cog touches. Replies land in the
    studio's timeline instead of going to Discord."""

    def __init__(
        self, studio: Studio, user_id: int, path: str, target: Ephemeral | None = None
    ) -> None:
        self.studio, self.path, self.target = studio, path, target
        self.user = SimpleNamespace(id=user_id, roles=[], display_name=USERS[user_id]["name"])
        self.channel = SimpleNamespace(name=CHANNELS[studio.view])
        self.command = SimpleNamespace(qualified_name=path)
        self.response = SimpleNamespace(
            defer=self._defer,
            send_message=self._send,
            edit_message=self._edit,
            is_done=lambda: self._done,
        )
        self.followup = SimpleNamespace(send=self._send)
        self._done = False
        self._pending: Ephemeral | None = None
        self.last: Ephemeral | None = None

    def _new(self) -> Ephemeral:
        s = self.studio
        s.sync()  # anything the bot already posted sits above this reply
        e = Ephemeral(self.user.id, f"/{self.path}", s.view, s.clock.now())
        s.timeline.append(e)
        return e

    async def _defer(self, **_: Any) -> None:
        # Discord puts the "thinking..." placeholder in the channel right away, so the
        # eventual reply sits above whatever the command goes on to post.
        self._done, self._pending = True, self._new()

    async def _send(self, content: str, *, view: Any = None, **_: Any) -> Any:
        e, self._pending, self._done = self._pending or self._new(), None, True
        e.content, e.view = content, view
        e.buttons = [(c.label, c.style.name) for c in view.children] if view else []
        self.last = e
        return SimpleNamespace(edit=MagicMock())

    async def _edit(self, *, content: str, view: Any = None) -> None:
        assert self.target is not None
        self.target.content, self.target.buttons = content, []


class Studio:
    """One scene: a bot wired to the fake gateway, plus the frames recorded so far."""

    def __init__(self, pool: asyncpg.Pool, title: str) -> None:
        self.clock = FakeClock(NOW)
        self.gw = DemoGateway(self.clock)
        self.svc = GameNightService(
            pool,
            self.gw,
            Settings(GUILD, NIGHT_CH, LIB_CH, LOG_CH, ADMIN_ROLE, TZ),
            self.clock,
            retry_sleep=_no_sleep,
        )
        cfg = Config("t", "postgresql://x", None, "g", "o/r", (), GUILD, NIGHT_CH, LIB_CH, LOG_CH,
                     ADMIN_ROLE, TZ, None)  # fmt: skip
        self.bot = TowerBot(cfg, pool, self.clock)
        self.cog = GameNightCog(self.bot, self.svc, MagicMock())
        self.title, self.caption, self.view = title, "", NIGHT_CH
        self.timeline: list[int | Ephemeral] = []
        self.frames: list[tuple[str, int]] = []
        self.commands: dict[str, dict[str, Any]] = {}
        self.composer: dict[str, Any] | None = None
        self.popup: dict[str, Any] | None = None

    async def start(self) -> None:
        await self.bot.add_cog(self.cog)
        for cmd in self.bot.tree.get_commands():
            d = cmd.to_dict(self.bot.tree)
            subs = [o for o in d.get("options", []) if o["type"] == 1]
            for sub in subs:
                self.commands[f"{d['name']} {sub['name']}"] = sub
            if not subs:
                self.commands[d["name"]] = d
        await self.svc.ensure_pins()  # what on_ready does, so the pins already exist

    # --- snapshots ----------------------------------------------------------
    def sync(self) -> None:
        seen = {m for m in self.timeline if isinstance(m, int)}
        self.timeline += [m for m in sorted(self.gw.messages) if m not in seen]

    def _time(self, at: datetime) -> str:
        d, today = at.astimezone(TZ), self.clock.now().astimezone(TZ).date()
        clock = f"{d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"
        if d.date() == today:
            return clock
        if (today - d.date()).days == 1:
            return f"Yesterday at {clock}"
        return f"{d.month}/{d.day}/{d:%y}, {clock}"

    def state(
        self,
        hot: tuple[int, str] | None = None,
        bump: tuple[int, str] | None = None,
        hot_button: str | None = None,
    ) -> dict[str, Any]:
        self.sync()
        items: list[dict[str, Any]] = []
        for it in self.timeline:
            if isinstance(it, Ephemeral):
                if it.channel != self.view:
                    continue
                items.append(
                    {
                        "kind": "ephemeral",
                        "user": str(it.user),
                        "command": it.command,
                        "time": self._time(it.at),
                        "content": it.content,
                        "buttons": [
                            {"label": label, "style": style, "hot": label == hot_button}
                            for label, style in it.buttons
                        ],
                    }
                )
                continue
            m = self.gw.messages[it]
            if m.channel_id != self.view:
                continue
            items.append(
                {
                    "kind": "bot",
                    "time": self._time(self.gw.posted_at[it]),
                    "content": m.content,
                    "embed": asdict(m.embed) if m.embed else None,
                    "edited": it in self.gw.edited,
                    "reply": "Click to see attachment" if m.reply_to else None,
                    "reactions": [
                        {
                            "emoji": e,
                            "count": len(users) + 1,  # the bot's own reaction
                            "me": YOU in users,
                            "hot": hot == (it, e),
                            "bump": bump == (it, e),
                        }
                        for e, users in m.reactions.items()
                    ],
                }
            )
        return {
            "title": self.title,
            "caption": self.caption,
            "channel": CHANNELS[self.view],
            "topic": TOPICS[self.view],
            "bot": BOT,
            "users": {str(k): v for k, v in USERS.items()},
            "channels": {str(k): v for k, v in CHANNELS.items()},
            "items": items,
            "composer": self.composer,
            "popup": self.popup,
        }

    def frame(self, ms: int, **marks: Any) -> None:
        state = json.dumps(self.state(**marks), ensure_ascii=False)
        if self.frames and self.frames[-1][0] == state:
            self.frames[-1] = (state, self.frames[-1][1] + ms)
        else:
            self.frames.append((state, ms))

    def say(self, caption: str, ms: int) -> None:
        self.caption = caption
        self.frame(ms)

    def card(self, title_prefix: str) -> int:
        """Message id of the newest card whose title starts with title_prefix."""
        return max(
            mid
            for mid, m in self.gw.messages.items()
            if m.embed is not None and m.embed.title.startswith(title_prefix)
        )

    # --- actions ------------------------------------------------------------
    def dismiss(self) -> None:
        """You click 'Dismiss message' on the private replies, so the cards stay in view."""
        self.timeline = [m for m in self.timeline if not isinstance(m, Ephemeral)]

    def _type(self, path: str, options: dict[str, Any]) -> None:
        full, meta = f"/{path}", self.commands[path]
        for i in [*range(2, len(full), 3), len(full)]:
            typed = full[:i]
            names = [path] + [n for n in self.commands if n != path]
            rows = [
                {
                    "name": f"/{n}",
                    "options": " ".join(o["name"] for o in self.commands[n].get("options", [])),
                    "description": self.commands[n]["description"],
                    "selected": n == path,
                }
                for n in names
                if f"/{n}".startswith(typed)
            ]
            self.composer, self.popup = {"typed": typed}, {"query": typed, "rows": rows[:4]}
            self.frame(90)
        self.frame(350)
        done: list[dict[str, str]] = []
        for name, value in options.items():
            hint = next(o for o in meta["options"] if o["name"] == name)
            self.popup = {"hint": {"name": name, "description": hint["description"]}}
            text = str(value)
            for i in [*range(0, len(text), 3), len(text)]:
                opts = [*done, {"name": name, "value": text[:i]}]
                self.composer = {"command": full, "options": opts, "editing": True}
                self.frame(90)
            self.frame(500)
            done.append({"name": name, "value": text})
        self.composer, self.popup = {"command": full, "options": done, "editing": False}, None
        self.frame(650)
        self.composer = None

    async def command(self, path: str, then: str, hold: int, **options: Any) -> Interaction:
        """You type a slash command and send it; `then` captions the result."""
        self.dismiss()
        self._type(path, options)
        itx = Interaction(self, YOU, path)
        try:
            await getattr(self.cog, CALLBACKS[path]).callback(self.cog, itx, **options)
        except Exception as e:  # same routing as the cog's app command error handler
            await self.cog.report_error(itx, e, f"/{path}")
        self.say(then, hold)
        return itx

    async def press(self, itx: Interaction, label: str, then: str, hold: int) -> None:
        """You click a button on the reply to `itx`."""
        assert itx.last is not None
        view = itx.last.view
        self.frame(700, hot_button=label)
        button = next(c for c in view.children if c.label == label)
        await button.callback(Interaction(self, YOU, itx.path, target=itx.last))
        self.say(then, hold)

    async def click(self, message_id: int, emoji: str, hold: int) -> None:
        """You react, and the bot re-renders the card."""
        self.frame(600, hot=(message_id, emoji))
        self.gw.react(message_id, emoji, YOU)
        self.frame(450)
        await self.svc.refresh_message(message_id)
        self.frame(hold)

    async def buddies(self, message_id: int, picks: dict[int, Sequence[str]], hold: int) -> None:
        for user, emojis in picks.items():
            for emoji in emojis:
                self.gw.react(message_id, emoji, user)
                self.frame(260, bump=(message_id, emoji))
            await self.svc.refresh_message(message_id)
            self.frame(520)
        self.frame(hold)

    async def posted_night(self) -> tuple[int, datetime]:
        """Off camera: a night for next Wednesday that the group already answered."""
        starts = default_start(self.clock.now(), TZ)
        await self.svc.create_night(YOU, starts)
        card = self.card(NIGHT_CARD)
        for user, emojis in {
            YOU: [IN, "2️⃣"],
            DRE: [IN, "2️⃣"],
            MAYA: [IN, "2️⃣", "3️⃣"],
            KOFI: [MAYBE, "1️⃣"],
            JULES: [OUT],
        }.items():
            for emoji in emojis:
                self.gw.react(card, emoji, user)
        await self.svc.refresh_message(card)
        return card, starts


# --- scenes -----------------------------------------------------------------
async def plan_a_night(s: Studio) -> None:
    s.say("Anyone can start one: type /gamenight new", 1700)
    await s.command(
        "gamenight new",
        then="Empty = next Wednesday 9 PM ET. You get a card and a server event",
        hold=2800,
    )
    card = s.card(NIGHT_CARD)
    s.caption = "Tap ✅ if you're in (❔ maybe, ❌ out)"
    await s.click(card, IN, 1300)
    s.caption = "Vote for games with the numbers, as many as you like"
    await s.click(card, "2️⃣", 1300)
    s.caption = "The card keeps score as your buddies react"
    await s.buddies(
        card,
        {DRE: [IN, "2️⃣"], MAYA: [IN, "2️⃣", "3️⃣"], KOFI: [MAYBE, "1️⃣"], JULES: [OUT]},
        600,
    )
    s.say("The leading game wears the 👑", 3500)


async def poll_for_a_time(s: Studio) -> None:
    s.say("Can't agree on a day? Offer 2 to 5 times with /gamenight poll", 1900)
    await s.command(
        "gamenight poll",
        times="wed 9pm, thu 9pm, sat 8pm",
        then="React with every time that works for you",
        hold=1800,
    )
    poll = s.card(POLL_CARD)
    await s.click(poll, "1️⃣", 500)
    await s.click(poll, "3️⃣", 900)
    await s.buddies(poll, {DRE: ["3️⃣"], MAYA: ["2️⃣", "3️⃣"], KOFI: ["3️⃣"], JULES: ["1️⃣", "3️⃣"]}, 900)
    s.say("Whoever posted the poll locks the winner with /gamenight pick", 1700)
    await s.command(
        "gamenight pick",
        option=3,
        then="The poll closes and a normal game night card takes over",
        hold=2000,
    )
    s.dismiss()
    s.frame(3000)


async def game_library(s: Studio) -> None:
    s.say("Got a game? /games suggest puts it in the vote rotation", 1800)
    await s.command(
        "games suggest",
        name="Lethal Company",
        then="New games go to the front of the line",
        hold=2000,
    )
    s.caption = "Typos and repeats get caught"
    itx = await s.command(
        "games suggest",
        name="Rocket Leage",
        then="Cancel, or Add anyway if it really is a different game",
        hold=2200,
    )
    await s.press(
        itx, "Cancel", then="Cancel, or Add anyway if it really is a different game", hold=1500
    )
    s.caption = "/games list shows the whole library"
    await s.command(
        "games list", then="Only you see the answer, so it never spams the chat", hold=2800
    )
    s.view = LIB_CH
    s.say("The pinned list in #game-library stays up to date on its own", 3800)


async def change_of_plans(s: Studio) -> None:
    await s.posted_night()
    s.say("Plans changed? The poster (or an admin) can move the night", 2000)
    await s.command(
        "gamenight move",
        when="sat 8pm",
        then="The card, the server event and the Next up pin all follow",
        hold=2800,
    )
    s.say("Group can't decide? Set the game with /gamenight game", 1600)
    await s.command(
        "gamenight game",
        number=1,
        then="The 👑 moves to your pick, whatever the votes say",
        hold=2800,
    )
    s.say("Not happening this week? /gamenight cancel", 1500)
    await s.command(
        "gamenight cancel", then="Card goes red and the server event is removed", hold=3800
    )


async def reminders(s: Studio) -> None:
    _, starts = await s.posted_night()
    s.say("Once a night is posted, the bot handles the rest", 2400)
    for lead, caption, hold in [
        (timedelta(hours=24), "24 hours before: a ping for everyone who's in or maybe", 3000),
        (timedelta(hours=1), "1 hour before: one more nudge", 2600),
        (timedelta(0), "At start time the winning game is locked in. Have fun!", 4200),
    ]:
        s.clock.set(starts - lead)
        await tick(s.svc, s.clock.now())  # one scheduler pass, as the bot runs every minute
        s.say(caption, hold)


SCENES: list[tuple[str, str, Callable[[Studio], Awaitable[None]]]] = [
    ("01-plan-a-night", "1 · Plan a game night", plan_a_night),
    ("02-poll-for-a-time", "2 · Poll for a time", poll_for_a_time),
    ("03-game-library", "3 · The game library", game_library),
    ("04-change-of-plans", "4 · Change of plans", change_of_plans),
    ("05-reminders", "5 · Reminders and game time", reminders),
]


# --- recording --------------------------------------------------------------
async def record(
    pool: asyncpg.Pool, title: str, scene: Callable[[Studio], Awaitable[None]]
) -> list[tuple[str, int]]:
    """Play one scene against a freshly seeded database and return its frames as
    (channel state as JSON, milliseconds on screen). Needs neither a browser nor
    Pillow, so tests/test_demo.py can keep the scenes working in CI."""
    async with pool.acquire() as c:
        await c.execute(f"truncate {TABLES} restart identity cascade")
        await c.executemany(
            "insert into games(name,name_norm,source,created_at,times_played,last_played)"
            " values($1,$2,'seed',$3,$4,$5)",
            [
                (n, normalize(n), datetime(2026, 7, 1, tzinfo=TZ), p, last)
                for n, p, last in SEED_GAMES
            ],
        )
    studio = Studio(pool, title)
    await studio.start()
    await scene(studio)
    errors = [m.embed.description for m in studio.gw.messages.values() if m.channel_id == LOG_CH]
    if errors:
        raise RuntimeError(f"{title}: the bot logged errors while recording: {errors}")
    return studio.frames


def write_gif(path: Path, shots: list[tuple[bytes, int]]) -> None:
    from PIL import Image  # demo extra, only needed to encode

    images = [Image.open(io.BytesIO(png)).convert("RGB") for png, _ in shots]
    # One palette for the whole clip, taken from frames spread across it, so colours
    # do not shimmer from frame to frame.
    sample = images[:: max(1, len(images) // 8)]
    sheet = Image.new("RGB", (WIDTH, HEIGHT * len(sample)))
    for i, im in enumerate(sample):
        sheet.paste(im, (0, i * HEIGHT))
    palette = sheet.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    frames = [im.quantize(palette=palette, dither=Image.Dither.NONE) for im in images]
    path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=[ms for _, ms in shots],
        loop=0,
        disposal=1,
    )


async def capture(scenes: dict[str, list[tuple[str, int]]], channel: str | None) -> None:
    from playwright.async_api import async_playwright  # demo extra, only needed to render

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel=channel)
        page = await browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        await page.goto(CHAT_URI)
        for name, frames in scenes.items():
            shots = []
            for state, ms in frames:
                await page.evaluate("s => window.render(JSON.parse(s))", state)
                shots.append((await page.screenshot(type="png"), ms))
            write_gif(OUT_DIR / f"{name}.gif", shots)
            seconds = sum(ms for _, ms in frames) / 1000
            print(f"{name}.gif  {len(frames)} frames  {seconds:.1f}s")
        await browser.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="render only scenes whose file name contains this")
    ap.add_argument("--channel", help="use an installed browser (e.g. chrome) instead of Chromium")
    args = ap.parse_args()

    base = os.environ.get(
        "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres"
    )
    name = f"tb_demo_{uuid.uuid4().hex[:8]}"
    url = base.rsplit("/", 1)[0] + "/" + name
    admin = await asyncpg.connect(base)
    await admin.execute(f'create database "{name}"')
    scenes: dict[str, list[tuple[str, int]]] = {}
    try:
        c = await asyncpg.connect(url)
        await apply_migrations(c)
        await c.close()
        pool = await create_pool(url, None)
        try:
            for key, title, scene in SCENES:
                if not args.only or args.only in key:
                    scenes[key] = await record(pool, title, scene)
        finally:
            await pool.close()
    finally:
        await admin.execute(f'drop database if exists "{name}" with (force)')
        await admin.close()
    await capture(scenes, args.channel)


if __name__ == "__main__":
    asyncio.run(main())
