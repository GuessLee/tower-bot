from __future__ import annotations

import asyncio
import logging
import os

from tower_bot.bot import TowerBot
from tower_bot.config import load_config
from tower_bot.core.db import apply_migrations, create_pool


async def run() -> None:
    cfg = load_config(os.environ)
    # If the DB is unreachable or a migration fails, this raises and the process
    # exits non-zero; the container restart policy retries.
    pool = await create_pool(cfg.database_url, cfg.db_password)
    try:
        async with pool.acquire() as c:
            applied = await apply_migrations(c)  # type: ignore[arg-type]
        logging.getLogger(__name__).info("migrations applied: %s", applied)
        async with TowerBot(cfg, pool) as bot:
            await bot.start(cfg.discord_token)
    finally:
        await pool.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
