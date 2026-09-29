from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from tower_bot.core.gateway import TransientGatewayError


async def with_retry[T](
    fn: Callable[[], Awaitable[T]],
    *,
    tries: int = 3,
    base_delay: float = 0.5,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    for attempt in range(tries):
        try:
            return await fn()
        except TransientGatewayError:
            if attempt == tries - 1:
                raise
            await sleep(base_delay * 2**attempt)
    raise AssertionError("unreachable")
