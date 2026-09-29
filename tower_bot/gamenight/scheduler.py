from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from tower_bot.gamenight.service import GameNightService


async def tick(
    svc: GameNightService,
    now: datetime,
    feedback_retry: Callable[[datetime], Awaitable[list[str]]] | None = None,
) -> None:
    """One scheduler pass. Each step is isolated so one failure never skips the others."""
    try:
        await svc.send_due_reminders(now)
    except Exception as e:
        await svc.log_error(f"reminders: {e!r}")

    try:
        nights = await svc.due_locks(now)
    except Exception as e:
        await svc.log_error(f"lock: {e!r}")
        nights = []

    for night in nights:
        try:
            await svc.lock_night(night.id)
        except Exception as e:
            await svc.log_error(f"lock night {night.id}: {e!r}")

    if feedback_retry is not None:
        try:
            for msg in await feedback_retry(now):
                await svc.log_error(f"Gave up filing {msg} after 24 h; text is in feedback_outbox.")
        except Exception as e:
            await svc.log_error(f"feedback retry: {e!r}")
