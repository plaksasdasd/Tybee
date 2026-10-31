# ruff: noqa: E402
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "support_bot"))

from app.bot import run as run_tybee
from support_bot.bot import run as run_support


async def main() -> None:
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(run_tybee())
        tasks.create_task(run_support())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
