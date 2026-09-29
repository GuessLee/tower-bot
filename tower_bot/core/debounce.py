from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)


class Debouncer:
    """Collapse bursts of pokes per key into one call, `delay` seconds after the last poke.

    Only a call that is still waiting out its delay is cancelled by a new poke. A call
    that has already started is left to finish (cancelling it mid-way could interrupt
    a multi-step refresh); the new poke simply schedules one more call after it.
    """

    def __init__(self, delay: float, fn: Callable[[int], Awaitable[None]]) -> None:
        self._delay = delay
        self._fn = fn
        self._waiting: dict[int, asyncio.Task[None]] = {}
        self._running: set[asyncio.Task[None]] = set()

    def poke(self, key: int) -> None:
        old = self._waiting.get(key)
        if old is not None and not old.done():
            old.cancel()
        self._waiting[key] = asyncio.create_task(self._run(key))

    async def _run(self, key: int) -> None:
        await asyncio.sleep(self._delay)
        me = asyncio.current_task()
        assert me is not None
        if self._waiting.get(key) is me:
            del self._waiting[key]  # past the cancellable window; also keeps the dict small
        self._running.add(me)
        try:
            await self._fn(key)
        except Exception:
            log.exception("debounced call failed for %s", key)
        finally:
            self._running.discard(me)

    async def drain(self) -> None:
        tasks = [t for t in (*self._waiting.values(), *self._running) if not t.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
