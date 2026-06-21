from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app import repositories as repo
from app.db.base import session_factory


class DbSessionMiddleware(BaseMiddleware):
    """Opens a DB session per update, loads/creates the user, commits on success."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        async with session_factory() as session:
            data["session"] = session
            if tg_user is not None:
                data["user"] = await repo.get_or_create_user(session, tg_user)
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise


def is_private(event: TelegramObject) -> bool:
    if isinstance(event, Message):
        return event.chat.type == "private"
    if isinstance(event, CallbackQuery) and event.message is not None:
        return event.message.chat.type == "private"
    return True
