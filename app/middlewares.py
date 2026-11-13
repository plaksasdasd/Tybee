from __future__ import annotations

import logging
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, TelegramObject

from app import repositories as repo
from app.covers import registry
from app.covers.loader import cover_text, cover_unban_fine
from app.db.base import session_factory
from app.db.models import User
from app.keyboards import inline

logger = logging.getLogger(__name__)


def _allowed_while_banned(event: TelegramObject) -> bool:
    """Updates a banned user may still send: the Robokassa payment flow."""
    if isinstance(event, CallbackQuery):
        return event.data == "unban:pay"
    return False


def _is_private_user_event(event: TelegramObject, data: dict[str, Any]) -> bool:
    if isinstance(event, Message):
        return event.chat.type == "private"
    if isinstance(event, CallbackQuery):
        message = event.message
        if message is not None:
            return message.chat.type == "private"
        chat = data.get("event_chat")
        if chat is not None:
            return getattr(chat, "type", "private") == "private"
        # Buttons older than ~48h arrive without a message — assume the
        # client's private dialog so the ban still applies.
        return True
    return False


async def _show_ban_screen(bot: Bot, user: User, cover_id: int) -> None:
    fine = cover_unban_fine(cover_id)
    try:
        await bot.send_message(
            user.id,
            cover_text(cover_id, "BANNED_SCREEN", fine=fine),
            parse_mode="HTML",
            reply_markup=inline.pay_fine(cover_id, fine),
        )
    except TelegramAPIError as exc:
        logger.info("Could not deliver ban screen to %s: %s", user.id, exc)


class DbSessionMiddleware(BaseMiddleware):
    """Opens a DB session per update, loads/creates the user and its per-cover
    state (``user_covers``), commits on success.

    One instance per cover (per dispatcher): ``data["cover"]`` / ``data["uc"]``
    always refer to the cover of the bot that received the update.

    Also acts as the ban guard: banned clients are intercepted before
    their update reaches any handler (except the unban payment flow).

    Must be registered as an OUTER middleware (``observer.outer_middleware``):
    inner middlewares run only after filters match a handler, so a guard there
    would silently skip every update no handler claims.
    """

    def __init__(self, cover_id: int) -> None:
        self.cover_id = cover_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        async with session_factory() as session:
            data["session"] = session
            cover = await repo.get_cover(session, self.cover_id)
            data["cover"] = cover
            if cover is not None:
                registry.cover_slug_var.set(cover.slug)
            user: User | None = None
            if tg_user is not None and cover is not None:
                user = await repo.get_or_create_user(session, tg_user)
                data["user"] = user
                uc = await repo.get_or_create_user_cover(session, user, cover.id)
                uc.last_seen_at = datetime.now(timezone.utc)
                data["uc"] = uc
            try:
                if (
                    user is not None
                    and user.is_banned
                    and _is_private_user_event(event, data)
                    and not _allowed_while_banned(event)
                ):
                    bot: Bot = data["bot"]
                    await _show_ban_screen(bot, user, self.cover_id)
                    if isinstance(event, CallbackQuery):
                        await event.answer()
                    result = None
                else:
                    result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
