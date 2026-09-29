import asyncio
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
        self.delay = 0.0
        # row_id -> html_url the adopt-search should report as already filed
        self.search_hits: dict[int, str] = {}
        self.search_calls: list[str] = []

    async def handler(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer tok"
        if req.url.path == "/search/issues":
            q = req.url.params.get("q", "")
            self.search_calls.append(q)
            for row_id, url in self.search_hits.items():
                if f"tower-bot-feedback:{row_id}" in q:
                    return httpx.Response(
                        200, json={"total_count": 1, "items": [{"html_url": url}]}
                    )
            return httpx.Response(200, json={"total_count": 0, "items": []})
        assert req.url.path == "/repos/GuessLee/unraid/issues"
        if self.delay:
            await asyncio.sleep(self.delay)
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
    b = feedback_body("hi", "Ann", "game-night", 42)
    assert "hi" in b and "Ann" in b and "#game-night" in b
    assert "<!-- tower-bot-feedback:42 -->" in b


def test_neutralizes_mentions() -> None:
    # An "@username" in feedback text, author or channel must not create a
    # live GitHub mention once filed as an issue title/body.
    t = feedback_title("cc @spammer please look at this")
    assert "@spammer" not in t
    assert "spammer" in t

    b = feedback_body("hey @octocat check this out", "@Ann", "game-night", 1)
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


async def test_retry_pending_overlap_files_once(pool: asyncpg.Pool) -> None:
    """Two retry_pending() sweeps overlapping on the same pending row (e.g. two
    scheduler ticks racing) must not each file their own GitHub issue: only one
    of them should win the row and POST."""
    hub = Hub()
    svc = make(pool, hub)
    hub.fail = 1000
    await svc.submit("racey", "Ann", "c", NOW)
    assert len(hub.seen) == 0

    hub.fail = 0
    hub.delay = 0.05  # keep both concurrent attempts in flight at once
    later = NOW + timedelta(minutes=11)
    results = await asyncio.gather(svc.retry_pending(later), svc.retry_pending(later))
    assert results == [[], []]
    assert len(hub.seen) == 1
    async with pool.acquire() as c:
        assert await c.fetchval("select status from feedback_outbox") == "filed"


async def test_retry_adopts_existing_issue(pool: asyncpg.Pool) -> None:
    """If a prior attempt's POST actually created the issue but the response
    was lost (timeout etc.), the next retry must find it via the hidden body
    marker and adopt it instead of filing a duplicate."""
    hub = Hub()
    svc = make(pool, hub)
    hub.fail = 1000
    await svc.submit("first", "Ann", "c", NOW)
    async with pool.acquire() as c:
        row_id = await c.fetchval("select id from feedback_outbox")

    hub.search_hits[row_id] = "https://github.com/x/adopted"
    result = await svc.retry_pending(NOW + timedelta(minutes=11))
    assert result == []
    assert len(hub.seen) == 0  # no duplicate POST
    assert any(f"tower-bot-feedback:{row_id}" in q for q in hub.search_calls)
    async with pool.acquire() as c:
        row = await c.fetchrow("select status, issue_url from feedback_outbox where id=$1", row_id)
    assert row is not None
    assert row["status"] == "filed"
    assert row["issue_url"] == "https://github.com/x/adopted"


async def test_retry_search_miss_falls_through_to_post(pool: asyncpg.Pool) -> None:
    """When the adopt-search finds nothing (total_count == 0), the retry must
    go on to actually file the issue, same as before."""
    hub = Hub()
    svc = make(pool, hub)
    hub.fail = 1000
    await svc.submit("second", "Bob", "c", NOW)
    async with pool.acquire() as c:
        row_id = await c.fetchval("select id from feedback_outbox")

    hub.fail = 0
    result = await svc.retry_pending(NOW + timedelta(minutes=11))
    assert result == []
    assert len(hub.seen) == 1
    assert any(f"tower-bot-feedback:{row_id}" in q for q in hub.search_calls)
    async with pool.acquire() as c:
        assert await c.fetchval("select status from feedback_outbox where id=$1", row_id) == "filed"
