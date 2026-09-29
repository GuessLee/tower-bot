from __future__ import annotations

import asyncio
import os
from datetime import timedelta

import httpx
import pytest

from tests.e2e.conftest import Driver
from tower_bot.bot import TowerBot

pytestmark = pytest.mark.e2e


async def test_night_rsvp_and_vote_live(bot: TowerBot, driver: Driver) -> None:
    svc = bot.service
    night = await svc.create_night(await driver.me(), svc.clock.now() + timedelta(days=3), "e2e")
    ch, mid = night.channel_id, night.message_id
    assert mid is not None
    await driver.react(ch, mid, "✅")
    await driver.react(ch, mid, "2️⃣")
    text = await driver.wait_embed(ch, mid, "In (1)")
    assert f"<@{await driver.me()}>" in text
    await driver.wait_embed(ch, mid, "1 vote 👑")
    await driver.unreact(ch, mid, "✅")
    await driver.wait_embed(ch, mid, "In (0)")
    await svc.cancel_night(0, [svc.settings.admin_role_id], night.id)


async def test_catch_up_after_restart(bot: TowerBot, driver: Driver) -> None:
    svc = bot.service
    night = await svc.create_night(1, svc.clock.now() + timedelta(days=3))
    ch, mid = night.channel_id, night.message_id
    assert mid is not None
    await bot.remove_cog("GameNightCog")  # stop listening, as if the bot were down
    await driver.react(ch, mid, "❔")
    await asyncio.sleep(4)
    assert "Maybe (1)" not in (await driver.wait_embed(ch, mid, "Maybe"))
    await svc.catch_up()
    await driver.wait_embed(ch, mid, "Maybe (1)")
    await svc.cancel_night(0, [svc.settings.admin_role_id], night.id)


async def test_scheduled_event_created_and_deleted(bot: TowerBot, driver: Driver) -> None:
    svc = bot.service
    night = await svc.create_night(1, svc.clock.now() + timedelta(days=4))
    assert night.event_id is not None
    g = int(os.environ["E2E_GUILD_ID"])
    r = await driver.c.get(f"/guilds/{g}/scheduled-events/{night.event_id}")
    assert r.status_code == 200
    await svc.cancel_night(0, [svc.settings.admin_role_id], night.id)
    r = await driver.c.get(f"/guilds/{g}/scheduled-events/{night.event_id}")
    assert r.status_code == 404


async def test_feedback_files_real_issue(bot: TowerBot) -> None:
    r = await bot.feedback.submit("e2e test feedback, auto-closed", "e2e", "e2e", bot.clock.now())
    assert r.url is not None
    number = r.url.rsplit("/", 1)[1]
    async with httpx.AsyncClient(
        base_url="https://api.github.com",
        headers={"Authorization": f"Bearer {os.environ['E2E_GITHUB_TOKEN']}"},
    ) as gh:
        issue = (await gh.get(f"/repos/GuessLee/unraid/issues/{number}")).json()
        assert {lbl["name"] for lbl in issue["labels"]} == {"feedback", "e2e-test"}
        await gh.patch(
            f"/repos/GuessLee/unraid/issues/{number}",
            json={"state": "closed", "state_reason": "not_planned"},
        )
