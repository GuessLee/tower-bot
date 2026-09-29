from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

import asyncpg
import httpx

from tower_bot.core.audit import audit

MAX_FEEDBACK = 2000
RETRY_EVERY = timedelta(minutes=10)
GIVE_UP_AFTER = timedelta(hours=24)


class GitHubError(Exception):
    pass


class GitHubIssues:
    def __init__(self, token: str, repo: str, client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._repo = repo
        self._c = client or httpx.AsyncClient(base_url="https://api.github.com", timeout=15)

    async def create(self, title: str, body: str, labels: Sequence[str]) -> str:
        r = await self._c.post(
            f"/repos/{self._repo}/issues",
            json={"title": title, "body": body, "labels": list(labels)},
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if r.status_code != 201:
            raise GitHubError(f"{r.status_code}: {r.text[:200]}")
        return str(r.json()["html_url"])


def _neutralize_mentions(text: str) -> str:
    """Break `@username` so filing it as a GitHub issue never pings that
    user: a zero-width space right after `@` keeps the character visible
    (so the text still reads naturally) without GitHub parsing it as a
    live mention."""
    return text.replace("@", "@\u200b")


def feedback_title(text: str) -> str:
    return _neutralize_mentions(f"Feedback (Discord): {' '.join(text.split())[:60]}")


def feedback_body(text: str, author: str, channel: str) -> str:
    return _neutralize_mentions(
        f"{text}\n\n---\nFrom **{author}** in #{channel} via tower-bot `/feedback`."
    )


@dataclass(frozen=True)
class FeedbackResult:
    url: str | None
    queued: bool


class FeedbackService:
    def __init__(self, pool: asyncpg.Pool, gh: GitHubIssues, labels: Sequence[str]) -> None:
        self.pool = pool
        self.gh = gh
        self.labels = list(labels)

    # asyncpg-stubs types Pool.acquire() as yielding PoolConnectionProxy, which
    # proxies every Connection method at runtime but isn't declared as a subtype.
    # audit() takes asyncpg.Connection, so we cast at the one call site (same
    # pattern as GameNightService._acquire in tower_bot/gamenight/service.py).
    def _acquire(self) -> AbstractAsyncContextManager[asyncpg.Connection]:
        return cast(AbstractAsyncContextManager[asyncpg.Connection], self.pool.acquire())

    async def _try(
        self,
        row_id: int,
        text: str,
        author: str,
        channel: str,
        labels: Sequence[str],
        now: datetime,
    ) -> str | None:
        try:
            url = await self.gh.create(
                feedback_title(text), feedback_body(text, author, channel), labels
            )
        except (GitHubError, httpx.HTTPError) as e:
            async with self._acquire() as c:
                await c.execute(
                    "update feedback_outbox set attempts=attempts+1, last_attempt_at=$2"
                    " where id=$1",
                    row_id,
                    now,
                )
                await audit(c, None, "feedback_attempt_failed", f"feedback:{row_id}", error=str(e))
            return None
        async with self._acquire() as c:
            await c.execute(
                "update feedback_outbox set status='filed', issue_url=$2,"
                " attempts=attempts+1, last_attempt_at=$3 where id=$1",
                row_id,
                url,
                now,
            )
            await audit(c, None, "feedback_filed", f"feedback:{row_id}", url=url)
        return url

    async def submit(self, text: str, author: str, channel: str, now: datetime) -> FeedbackResult:
        clean = text.strip()
        if not clean:
            raise ValueError("Feedback is empty.")
        if len(clean) > MAX_FEEDBACK:
            raise ValueError(f"Feedback is limited to {MAX_FEEDBACK} characters.")
        async with self._acquire() as c:
            row_id = await c.fetchval(
                "insert into feedback_outbox(text,author,channel,labels,created_at)"
                " values($1,$2,$3,$4,$5) returning id",
                clean,
                author,
                channel,
                self.labels,
                now,
            )
        url = await self._try(row_id, clean, author, channel, self.labels, now)
        return FeedbackResult(url=url, queued=url is None)

    async def retry_pending(self, now: datetime) -> list[str]:
        async with self._acquire() as c:
            rows = await c.fetch(
                "select * from feedback_outbox where status='pending'"
                " and (last_attempt_at is null or last_attempt_at <= $1) order by id",
                now - RETRY_EVERY,
            )
        gave_up: list[str] = []
        for r in rows:
            if now - r["created_at"] > GIVE_UP_AFTER:
                async with self._acquire() as c:
                    await c.execute(
                        "update feedback_outbox set status='failed' where id=$1", r["id"]
                    )
                    await audit(c, None, "feedback_gave_up", f"feedback:{r['id']}")
                gave_up.append(f"feedback #{r['id']} from {r['author']}")
                continue
            await self._try(r["id"], r["text"], r["author"], r["channel"], r["labels"], now)
        return gave_up
