import asyncio

from tower_bot.core.debounce import Debouncer


async def test_collapses_bursts_per_key() -> None:
    seen: list[int] = []

    async def fn(key: int) -> None:
        seen.append(key)

    d = Debouncer(0.05, fn)
    for _ in range(5):
        d.poke(1)
    d.poke(2)
    await asyncio.sleep(0.15)
    await d.drain()
    assert sorted(seen) == [1, 2]


async def test_errors_do_not_escape() -> None:
    async def boom(key: int) -> None:
        raise RuntimeError("x")

    d = Debouncer(0.01, boom)
    d.poke(1)
    await asyncio.sleep(0.05)
    await d.drain()  # no exception


async def test_poke_does_not_cancel_a_call_already_running() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished: list[int] = []

    async def slow(key: int) -> None:
        started.set()
        await release.wait()
        finished.append(key)

    d = Debouncer(0.01, slow)
    d.poke(1)
    await started.wait()
    d.poke(1)  # arrives mid-call: must not cancel it, must schedule one more
    release.set()
    await asyncio.sleep(0.05)
    await d.drain()
    assert finished == [1, 1]
