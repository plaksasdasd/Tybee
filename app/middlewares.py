from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message, TelegramObject

from app import repositories as repo
from app import texts
from app.config import get_settings
from app.db.base import session_factory
from app.db.models import User
from app.keyboards import inline


def _is_admin(user: User) -> bool:
    return user.is_admin or user.id in get_settings().admin_ids


def _allowed_while_banned(event: TelegramObject) -> bool:
    """Updates a banned user may still send: the unban payment flow."""
    if isinstance(event, Message):
        return event.successful_payment is not None
    if isinstance(event, CallbackQuery):
        return event.data == "unban:pay"
    return False


def _is_private_user_event(event: TelegramObject) -> bool:
    if isinstance(event, Message):
        return event.chat.type == "private"
    if isinstance(event, CallbackQuery) and event.message is not None:
        return event.message.chat.type == "private"
    return False


async def _show_ban_screen(bot: Bot, user: User) -> None:
    fine = get_settings().unban_fine_stars
    await bot.send_message(
        user.id,
        texts.BANNED_SCREEN.format(fine=fine),
        parse_mode="HTML",
        reply_markup=inline.pay_fine(fine),
    )


class DbSessionMiddleware(BaseMiddleware):
    """Opens a DB session per update, loads/creates the user, commits on success.

    Also acts as the ban guard: banned non-admin clients are intercepted before
    their update reaches any handler (except the unban payment flow).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        async with session_factory() as session:
            data["session"] = session
            user: User | None = None
            if tg_user is not None:
                user = await repo.get_or_create_user(session, tg_user)
                data["user"] = user
            try:
                if (
                    user is not None
                    and user.is_banned
                    and not _is_admin(user)
                    and _is_private_user_event(event)
                    and not _allowed_while_banned(event)
                ):
                    bot: Bot = data["bot"]
                    await _show_ban_screen(bot, user)
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


def is_private(event: TelegramObject) -> bool:
    if isinstance(event, Message):
        return event.chat.type == "private"
    if isinstance(event, CallbackQuery) and event.message is not None:
        return event.message.chat.type == "private"
    return True
