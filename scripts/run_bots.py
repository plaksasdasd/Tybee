# ruff: noqa: E402
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.bot import run as run_tybee


async def main() -> None:
    # Support lives in the web panel (site/); this process runs the cover bots and workers only.
    await run_tybee()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
