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


async def test_calls_for_one_key_never_overlap_and_latest_runs_last() -> None:
    release = asyncio.Event()
    active = 0
    max_active = 0
    order: list[str] = []
    calls = 0

    async def fn(key: int) -> None:
        nonlocal active, max_active, calls
        calls += 1
        n = calls
        active += 1
        max_active = max(max_active, active)
        order.append(f"start{n}")
        if n == 1:
            await release.wait()
        order.append(f"end{n}")
        active -= 1

    d = Debouncer(0.01, fn)
    d.poke(1)
    await asyncio.sleep(0.03)  # first call started and is blocked
    d.poke(1)
    await asyncio.sleep(0.05)  # second delay expired while the first is still blocked
    assert order == ["start1"]  # the second call has not started
    release.set()
    await asyncio.sleep(0.02)
    await d.drain()
    assert order == ["start1", "end1", "start2", "end2"]
    assert max_active == 1


async def test_pokes_queued_behind_a_running_call_collapse_to_one() -> None:
    release = asyncio.Event()
    calls = 0

    async def fn(key: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            await release.wait()

    d = Debouncer(0.01, fn)
    d.poke(1)
    await asyncio.sleep(0.03)
    for _ in range(3):  # each waits out its delay, then queues behind the running call
        d.poke(1)
        await asyncio.sleep(0.03)
    release.set()
    await asyncio.sleep(0.02)
    await d.drain()
    assert calls == 2
