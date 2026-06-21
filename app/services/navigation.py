"""Single-screen navigation engine.

The bot keeps exactly one "screen" message per user. Navigating the menu
*edits* that message (photo + caption + keyboard) instead of deleting it and
sending a new one, so the chat history stays clean.
"""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    FSInputFile,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.assets import Banner
from app.db.models import User

logger = logging.getLogger(__name__)


def _is_bad_file_id(exc: TelegramBadRequest) -> bool:
    return "file identifier" in str(exc).lower()


async def _cache_from(session: AsyncSession, banner: Banner, message: Message | bool) -> None:
    if isinstance(message, Message) and message.photo:
        await repo.set_cached_file_id(session, banner.value, message.photo[-1].file_id)


async def _send_fresh(
    bot: Bot,
    session: AsyncSession,
    user: User,
    banner: Banner,
    caption: str,
    keyboard: InlineKeyboardMarkup | None,
) -> None:
    """Send a brand-new screen message straight from disk and remember its id."""
    sent = await bot.send_photo(
        chat_id=user.id,
        photo=FSInputFile(banner.path),
        caption=caption,
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await repo.set_screen(session, user, sent.message_id)
    await _cache_from(session, banner, sent)


async def show_screen(
    bot: Bot,
    session: AsyncSession,
    user: User,
    banner: Banner,
    caption: str,
    keyboard: InlineKeyboardMarkup | None = None,
    force_new: bool = False,
) -> None:
    """Render a screen for the user by editing the existing message when possible.

    With ``force_new`` the screen is posted as a brand-new message at the bottom
    of the chat instead of being edited in place. This is used right after the
    user sends a text message (e.g. /start or an article code): editing a message
    that now sits above the user's text would look unresponsive.

    Falls back gracefully: if a cached file_id is stale/invalid it is dropped and
    the banner is re-uploaded from disk; if the message can't be edited at all a
    new one is sent.
    """
    if force_new:
        await _send_fresh(bot, session, user, banner, caption, keyboard)
        return

    cached_id = await repo.get_cached_file_id(session, banner.value)
    media_source: str | FSInputFile = cached_id or FSInputFile(banner.path)

    if user.screen_message_id is not None:
        try:
            result = await bot.edit_message_media(
                chat_id=user.id,
                message_id=user.screen_message_id,
                media=InputMediaPhoto(media=media_source, caption=caption, parse_mode="HTML"),
                reply_markup=keyboard,
            )
            await _cache_from(session, banner, result)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            if cached_id is not None and _is_bad_file_id(exc):
                # Stale cached file_id — drop it and retry the edit from disk.
                await repo.set_cached_file_id(session, banner.value, "")
                try:
                    result = await bot.edit_message_media(
                        chat_id=user.id,
                        message_id=user.screen_message_id,
                        media=InputMediaPhoto(
                            media=FSInputFile(banner.path), caption=caption, parse_mode="HTML"
                        ),
                        reply_markup=keyboard,
                    )
                    await _cache_from(session, banner, result)
                    return
                except TelegramBadRequest as retry_exc:
                    logger.info("Edit retry failed for %s, resending: %s", user.id, retry_exc)
            else:
                logger.info("Could not edit screen for %s, resending: %s", user.id, exc)

    await _send_fresh(bot, session, user, banner, caption, keyboard)
