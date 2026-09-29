from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)


class Debouncer:
    """Collapse bursts of pokes per key into one call, `delay` seconds after the last poke.

    Calls for one key never overlap: they are serialized by a per-key lock, so a slow
    call (for example one stuck in retries) can never finish after, and overwrite, a
    newer one. A poke cancels the key's pending call as long as that call has not
    started yet (still in its delay, or queued behind a running call); a call that has
    started is left to finish, and the newest poke then runs after it, last.
    """

    def __init__(self, delay: float, fn: Callable[[int], Awaitable[None]]) -> None:
        self._delay = delay
        self._fn = fn
        self._pending: dict[int, asyncio.Task[None]] = {}
        self._running: set[asyncio.Task[None]] = set()
        self._locks: dict[int, asyncio.Lock] = {}
        self._users: dict[int, int] = {}  # tasks holding a reference to each key's lock

    def poke(self, key: int) -> None:
        old = self._pending.get(key)
        if old is not None and not old.done():
            old.cancel()
        self._pending[key] = asyncio.create_task(self._run(key))

    async def _run(self, key: int) -> None:
        me = asyncio.current_task()
        assert me is not None
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._users[key] = self._users.get(key, 0) + 1
        try:
            await asyncio.sleep(self._delay)
            async with lock:
                if self._pending.get(key) is me:
                    del self._pending[key]  # started: no longer cancellable by a poke
                self._running.add(me)
                try:
                    await self._fn(key)
                except Exception:
                    log.exception("debounced call failed for %s", key)
                finally:
                    self._running.discard(me)
        finally:
            self._users[key] -= 1
            if self._users[key] == 0:  # nobody else waits on this key: drop its lock
                del self._users[key]
                del self._locks[key]

    async def drain(self) -> None:
        tasks = [t for t in (*self._pending.values(), *self._running) if not t.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
