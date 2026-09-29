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

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def create(self, title: str, body: str, labels: Sequence[str]) -> str:
        r = await self._c.post(
            f"/repos/{self._repo}/issues",
            json={"title": title, "body": body, "labels": list(labels)},
            headers=self._headers(),
        )
        if r.status_code != 201:
            raise GitHubError(f"{r.status_code}: {r.text[:200]}")
        return str(r.json()["html_url"])

    async def find_existing(self, row_id: int) -> str | None:
        """Best-effort lookup for an issue a previous attempt already filed
        for this outbox row, keyed on the hidden body marker (see
        feedback_body). Used only on retries, before POSTing, to avoid
        double-filing when a prior POST succeeded on GitHub's side but its
        response never reached us (timeout, connection drop, etc.). Any
        failure here (network, non-200, unexpected shape) just returns None
        so the caller falls through to a normal POST; at-least-once filing
        is acceptable, duplicate-on-every-hiccup is not worth guarding
        further than this."""
        query = f'repo:{self._repo} in:body "{_marker_text(row_id)}"'
        try:
            r = await self._c.get("/search/issues", params={"q": query}, headers=self._headers())
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        try:
            data = r.json()
            items = data["items"]
            if data["total_count"] > 0 and items:
                return str(items[0]["html_url"])
        except (ValueError, KeyError, IndexError, TypeError):
            return None
        return None


def _neutralize_mentions(text: str) -> str:
    """Break `@username` so filing it as a GitHub issue never pings that
    user: a zero-width space right after `@` keeps the character visible
    (so the text still reads naturally) without GitHub parsing it as a
    live mention."""
    return text.replace("@", "@\u200b")


def _marker_text(row_id: int) -> str:
    return f"tower-bot-feedback:{row_id}"


def feedback_title(text: str) -> str:
    return _neutralize_mentions(f"Feedback (Discord): {' '.join(text.split())[:60]}")


def feedback_body(text: str, author: str, channel: str, row_id: int) -> str:
    # The hidden marker lets a later retry find an issue a prior attempt
    # already filed (see GitHubIssues.find_existing) so it can adopt it
    # instead of filing a duplicate when a POST's response was lost.
    return _neutralize_mentions(
        f"{text}\n\n---\nFrom **{author}** in #{channel} via tower-bot `/feedback`.\n"
        f"<!-- {_marker_text(row_id)} -->"
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
        *,
        is_retry: bool,
    ) -> str | None:
        if is_retry:
            adopted = await self.gh.find_existing(row_id)
            if adopted is not None:
                async with self._acquire() as c:
                    await c.execute(
                        "update feedback_outbox set status='filed', issue_url=$2,"
                        " attempts=attempts+1, last_attempt_at=$3 where id=$1",
                        row_id,
                        adopted,
                        now,
                    )
                    await audit(c, None, "feedback_adopted", f"feedback:{row_id}", url=adopted)
                return adopted
        try:
            url = await self.gh.create(
                feedback_title(text), feedback_body(text, author, channel, row_id), labels
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
        url = await self._try(row_id, clean, author, channel, self.labels, now, is_retry=False)
        return FeedbackResult(url=url, queued=url is None)

    async def retry_pending(self, now: datetime) -> list[str]:
        threshold = now - RETRY_EVERY
        async with self._acquire() as c:
            candidates = await c.fetch(
                "select id, created_at from feedback_outbox where status='pending'"
                " and (last_attempt_at is null or last_attempt_at <= $1) order by id",
                threshold,
            )
        gave_up: list[str] = []
        for cand in candidates:
            if now - cand["created_at"] > GIVE_UP_AFTER:
                # Conditional on status='pending' so that if a concurrent
                # retry_pending() sweep already gave up on (or filed) this
                # row, we don't report it a second time.
                async with self._acquire() as c:
                    failed = await c.fetchrow(
                        "update feedback_outbox set status='failed'"
                        " where id=$1 and status='pending' returning id, author",
                        cand["id"],
                    )
                    if failed is not None:
                        await audit(c, None, "feedback_gave_up", f"feedback:{failed['id']}")
                if failed is not None:
                    gave_up.append(f"feedback #{failed['id']} from {failed['author']}")
                continue
            # Atomically claim the row before trying it: re-checks status and
            # the retry-cooldown against the row's *current* state, so an
            # overlapping retry_pending() sweep that raced us to the same
            # candidate list gets nothing back here and skips the row instead
            # of also calling GitHub for it.
            async with self._acquire() as c:
                claimed = await c.fetchrow(
                    "update feedback_outbox set last_attempt_at=$2"
                    " where id=$1 and status='pending'"
                    " and (last_attempt_at is null or last_attempt_at <= $3)"
                    " returning *",
                    cand["id"],
                    now,
                    threshold,
                )
            if claimed is None:
                continue
            await self._try(
                claimed["id"],
                claimed["text"],
                claimed["author"],
                claimed["channel"],
                claimed["labels"],
                now,
                is_retry=True,
            )
        return gave_up
