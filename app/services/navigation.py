"""Single-screen navigation engine.

The bot keeps exactly one "screen" message per user. Navigating the menu
*edits* that message (photo + caption + keyboard, or plain text for screens
without a banner) instead of deleting it and sending a new one, so the chat
history stays clean.
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


async def _drop_screen(bot: Bot, session: AsyncSession, user: User) -> None:
    """Best-effort removal of the stale screen message before replacing it.

    Needed when the next screen is of a different kind (text <-> photo): those
    cannot be converted by an edit, so the old message must go.
    """
    if user.screen_message_id is None:
        return
    try:
        await bot.delete_message(chat_id=user.id, message_id=user.screen_message_id)
    except TelegramBadRequest:
        pass
    await repo.set_screen(session, user, None)


async def _send_fresh_text(
    bot: Bot,
    session: AsyncSession,
    user: User,
    caption: str,
    keyboard: InlineKeyboardMarkup | None,
) -> None:
    sent = await bot.send_message(
        chat_id=user.id, text=caption, parse_mode="HTML", reply_markup=keyboard
    )
    await repo.set_screen(session, user, sent.message_id)


async def _send_fresh(
    bot: Bot,
    session: AsyncSession,
    user: User,
    banner: Banner,
    caption: str,
    keyboard: InlineKeyboardMarkup | None,
    photo: str | None = None,
) -> None:
    """Send a brand-new screen message and remember its id.

    ``photo`` is an optional Telegram file_id overriding the banner (e.g. a
    product photo uploaded by an admin). Otherwise a cached banner file_id is
    reused when possible — re-uploading the PNG on every screen makes each
    reply noticeably slower.
    """
    cached_id = None if photo else await repo.get_cached_file_id(session, banner.value)
    try:
        sent = await bot.send_photo(
            chat_id=user.id,
            photo=photo or cached_id or FSInputFile(banner.path),
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except TelegramBadRequest as exc:
        if cached_id is None or not _is_bad_file_id(exc):
            raise
        # Stale cached file_id — drop it and re-upload the banner from disk.
        await repo.set_cached_file_id(session, banner.value, "")
        sent = await bot.send_photo(
            chat_id=user.id,
            photo=FSInputFile(banner.path),
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    await repo.set_screen(session, user, sent.message_id)
    if photo is None:
        await _cache_from(session, banner, sent)


async def show_screen(
    bot: Bot,
    session: AsyncSession,
    user: User,
    banner: Banner | None,
    caption: str,
    keyboard: InlineKeyboardMarkup | None = None,
    force_new: bool = False,
    photo: str | None = None,
) -> None:
    """Render a screen for the user by editing the existing message when possible.

    ``banner=None`` renders a text-only screen (no image). Switching between a
    text and a photo screen deletes the old message and posts a new one —
    Telegram cannot convert one into the other in place.

    With ``force_new`` the screen is posted as a brand-new message at the bottom
    of the chat instead of being edited in place. This is used right after the
    user sends a text message (e.g. /start or an article code): editing a message
    that now sits above the user's text would look unresponsive.

    Falls back gracefully: if a cached file_id is stale/invalid it is dropped and
    the banner is re-uploaded from disk; if the message can't be edited at all a
    new one is sent (and the uneditable one removed).
    """
    if banner is None:
        if not force_new and user.screen_message_id is not None:
            try:
                await bot.edit_message_text(
                    text=caption,
                    chat_id=user.id,
                    message_id=user.screen_message_id,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                return
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc).lower():
                    return
                logger.info("Could not edit text screen for %s, resending: %s", user.id, exc)
        await _drop_screen(bot, session, user)
        await _send_fresh_text(bot, session, user, caption, keyboard)
        return

    if force_new:
        await _send_fresh(bot, session, user, banner, caption, keyboard, photo=photo)
        return

    cached_id = None if photo else await repo.get_cached_file_id(session, banner.value)
    media_source: str | FSInputFile = photo or cached_id or FSInputFile(banner.path)

    if user.screen_message_id is not None:
        try:
            result = await bot.edit_message_media(
                chat_id=user.id,
                message_id=user.screen_message_id,
                media=InputMediaPhoto(media=media_source, caption=caption, parse_mode="HTML"),
                reply_markup=keyboard,
            )
            if photo is None:
                await _cache_from(session, banner, result)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            if cached_id is not None and _is_bad_file_id(exc):
                # Stale cached file_id — drop it and retry the edit from disk.
                await repo.delete_cached_file_id(session, banner.value)
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

    # The old screen is of another kind (or uneditable) — replace it entirely.
    await _drop_screen(bot, session, user)
    await _send_fresh(bot, session, user, banner, caption, keyboard, photo=photo)
