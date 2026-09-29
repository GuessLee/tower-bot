import json
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest

from tower_bot.gamenight.feedback import (
    FeedbackService,
    GitHubIssues,
    feedback_body,
    feedback_title,
)

NOW = datetime(2026, 9, 28, 21, 0, tzinfo=UTC)


class Hub:
    def __init__(self) -> None:
        self.fail = 0
        self.seen: list[dict[str, object]] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/repos/GuessLee/unraid/issues"
        assert req.headers["Authorization"] == "Bearer tok"
        if self.fail > 0:
            self.fail -= 1
            return httpx.Response(502, text="bad gateway")
        body = json.loads(req.content)
        self.seen.append(body)
        return httpx.Response(201, json={"html_url": f"https://github.com/x/{len(self.seen)}"})


def make(pool: asyncpg.Pool, hub: Hub) -> FeedbackService:
    client = httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(hub.handler)
    )
    return FeedbackService(
        pool, GitHubIssues("tok", "GuessLee/unraid", client), ["feedback", "classify-me"]
    )


def test_title_and_body() -> None:
    t = feedback_title("The   card\nis broken " + "x" * 100)
    assert t.startswith("Feedback (Discord): The card is broken")
    assert len(t) == len("Feedback (Discord): ") + 60
    b = feedback_body("hi", "Ann", "game-night")
    assert "hi" in b and "Ann" in b and "#game-night" in b


def test_neutralizes_mentions() -> None:
    # An "@username" in feedback text, author or channel must not create a
    # live GitHub mention once filed as an issue title/body.
    t = feedback_title("cc @spammer please look at this")
    assert "@spammer" not in t
    assert "spammer" in t

    b = feedback_body("hey @octocat check this out", "@Ann", "game-night")
    assert "@octocat" not in b
    assert "octocat" in b
    assert "@Ann" not in b
    assert "Ann" in b


async def test_submit_files_issue(pool: asyncpg.Pool) -> None:
    hub = Hub()
    r = await make(pool, hub).submit("  lobby is slow ", "Ann", "game-night", NOW)
    assert r.url == "https://github.com/x/1" and not r.queued
    assert hub.seen[0]["labels"] == ["feedback", "classify-me"]
    assert hub.seen[0]["title"] == "Feedback (Discord): lobby is slow"


@pytest.mark.parametrize("bad", ["", "   ", "x" * 2001])
async def test_submit_rejects(pool: asyncpg.Pool, bad: str) -> None:
    with pytest.raises(ValueError):
        await make(pool, Hub()).submit(bad, "Ann", "c", NOW)


async def test_queue_retry_and_give_up(pool: asyncpg.Pool) -> None:
    hub = Hub()
    svc = make(pool, hub)
    hub.fail = 100
    r = await svc.submit("first", "Ann", "c", NOW)
    assert r.queued and r.url is None
    assert await svc.retry_pending(NOW + timedelta(minutes=5)) == []  # too soon, no call
    hub.fail = 0
    assert await svc.retry_pending(NOW + timedelta(minutes=11)) == []
    assert len(hub.seen) == 1
    async with pool.acquire() as c:
        assert await c.fetchval("select status from feedback_outbox") == "filed"

    hub.fail = 1000
    await svc.submit("second", "Bob", "c", NOW)
    gave_up = await svc.retry_pending(NOW + timedelta(hours=25))
    assert len(gave_up) == 1 and "Bob" in gave_up[0]
    async with pool.acquire() as c:
        assert await c.fetchval("select status from feedback_outbox where author='Bob'") == "failed"


async def test_network_error_queues(pool: asyncpg.Pool) -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client = httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(boom)
    )
    svc = FeedbackService(pool, GitHubIssues("tok", "GuessLee/unraid", client), ["feedback"])
    assert (await svc.submit("x", "Ann", "c", NOW)).queued
