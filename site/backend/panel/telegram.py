from __future__ import annotations

import re

from aiogram import Bot

from app.covers import spec
from app.covers.service import CoverError

_TOKEN_RE = re.compile(spec.TOKEN_RE)


async def fetch_bot_username(token: str) -> str:
    """Validate a bot token with ``getMe``; returns the bot's @username."""
    if not _TOKEN_RE.match(token or ""):
        raise CoverError("неверный формат токена бота", status=422)
    bot = Bot(token)
    try:
        me = await bot.get_me()
    except Exception as exc:  # noqa: BLE001
        raise CoverError("Telegram не принял токен (getMe не удался)", status=422) from exc
    finally:
        await bot.session.close()
    return me.username or ""
