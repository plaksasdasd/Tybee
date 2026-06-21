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


async def _photo_input(session: AsyncSession, banner: Banner):
    file_id = await repo.get_cached_file_id(session, banner.value)
    if file_id:
        return file_id
    return FSInputFile(banner.path)


async def show_screen(
    bot: Bot,
    session: AsyncSession,
    user: User,
    banner: Banner,
    caption: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    """Render a screen for the user by editing the existing message when possible."""
    media_source = await _photo_input(session, banner)

    if user.screen_message_id is not None:
        try:
            result = await bot.edit_message_media(
                chat_id=user.id,
                message_id=user.screen_message_id,
                media=InputMediaPhoto(media=media_source, caption=caption, parse_mode="HTML"),
                reply_markup=keyboard,
            )
            if isinstance(result, Message) and result.photo:
                await repo.set_cached_file_id(session, banner.value, result.photo[-1].file_id)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                # Caption/markup identical — nothing to do.
                return
            logger.info("Could not edit screen for %s, resending: %s", user.id, exc)

    sent = await bot.send_photo(
        chat_id=user.id,
        photo=media_source,
        caption=caption,
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await repo.set_screen(session, user, sent.message_id)
    if sent.photo:
        await repo.set_cached_file_id(session, banner.value, sent.photo[-1].file_id)
