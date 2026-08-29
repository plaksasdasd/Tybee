"""Single-screen navigation engine.

The bot keeps exactly one "screen" message per user. Navigating the menu
*edits* that message (photo + caption + keyboard) instead of deleting it and
sending a new one, so the chat history stays clean.

Every screen carries a banner, so the message is always a photo and can always
be edited in place via ``edit_message_media``.
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

    Needed only for uneditable leftovers — e.g. a plain-text screen saved by an
    older bot version: such a message cannot be converted to a photo by an
    edit, so it must go.
    """
    if user.screen_message_id is None:
        return
    try:
        await bot.delete_message(chat_id=user.id, message_id=user.screen_message_id)
    except TelegramBadRequest:
        pass
    await repo.set_screen(session, user, None)


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

    With ``force_new`` the screen is posted as a brand-new message at the bottom
    of the chat instead of being edited in place. This is used right after the
    user sends a text message (e.g. /start or an article code): editing a message
    that now sits above the user's text would look unresponsive.

    Falls back gracefully: if a cached file_id is stale/invalid it is dropped and
    the banner is re-uploaded from disk; if the message can't be edited at all
    (e.g. a legacy text screen saved by an older bot version) a new one is sent
    and the uneditable one removed.
    """
    if banner is None:
        banner = Banner.WELCOME

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
