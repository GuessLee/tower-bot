import pytest

from tower_bot.core.gateway import MessageGone, TransientGatewayError
from tower_bot.core.retry import with_retry


async def no_sleep(_: float) -> None:
    return None


async def test_retries_then_succeeds() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TransientGatewayError("503")
        return "ok"

    assert await with_retry(fn, sleep=no_sleep) == "ok"
    assert calls == 3


async def test_gives_up_after_three() -> None:
    calls = 0

    async def fn() -> None:
        nonlocal calls
        calls += 1
        raise TransientGatewayError("503")

    with pytest.raises(TransientGatewayError):
        await with_retry(fn, sleep=no_sleep)
    assert calls == 3


async def test_message_gone_not_retried() -> None:
    calls = 0

    async def fn() -> None:
        nonlocal calls
        calls += 1
        raise MessageGone()

    with pytest.raises(MessageGone):
        await with_retry(fn, sleep=no_sleep)
    assert calls == 1
