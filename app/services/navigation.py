"""Single-screen navigation engine.

The bot keeps exactly one "screen" message per (client x cover). Navigating the
menu *edits* that message (photo + caption + keyboard) instead of deleting it
and sending a new one, so the chat history stays clean.

Every screen carries a banner, so the message is always a photo and can always
be edited in place via ``edit_message_media``.

Banners are per cover: ``<cover>/banners/<name>.png``. The Telegram ``file_id``
of an uploaded banner is cached in ``cover_media_cache`` together with the
file's fingerprint (``"{size}:{mtime_ns}"``); when the file changes on disk the
banner is uploaded again, so a replaced banner shows up on the next screen.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

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
from app.covers.loader import banner_fingerprint
from app.db.models import User, UserCover

logger = logging.getLogger(__name__)


def _is_bad_file_id(exc: TelegramBadRequest) -> bool:
    return "file identifier" in str(exc).lower()


@dataclass
class _Media:
    """What to send as the screen's photo and how to remember its file_id."""

    source: str | FSInputFile
    banner: Banner | None  # None -> custom photo (e.g. product photo)
    fingerprint: str = ""
    cached: bool = False  # source is a cached file_id
    product_id: int | None = None  # cache the result as this product's photo


async def _banner_media(session: AsyncSession, cover_id: int, banner: Banner) -> _Media:
    path = banner.path_for(cover_id)
    fingerprint = banner_fingerprint(path)
    cached = await repo.get_cached_banner(session, cover_id, banner.value)
    if cached is not None and cached.fingerprint == fingerprint:
        return _Media(cached.file_id, banner, fingerprint, cached=True)
    return _Media(FSInputFile(path), banner, fingerprint)


async def _remember(
    session: AsyncSession, cover_id: int, media: _Media, message: Message | bool
) -> None:
    if not (isinstance(message, Message) and message.photo):
        return
    file_id = message.photo[-1].file_id
    if media.banner is not None:
        await repo.set_cached_banner(
            session, cover_id, media.banner.value, file_id, media.fingerprint
        )
    elif media.product_id is not None and isinstance(media.source, FSInputFile):
        await repo.set_product_photo(session, media.product_id, cover_id, file_id)


async def _forget(session: AsyncSession, cover_id: int, media: _Media) -> None:
    """Drop a stale file_id (only this cover's record)."""
    if media.banner is not None:
        await repo.delete_cached_banner(session, cover_id, media.banner.value)
    elif media.product_id is not None:
        await repo.delete_product_photo(session, media.product_id, cover_id)


async def _drop_screen(bot: Bot, session: AsyncSession, user: User, uc: UserCover) -> None:
    """Best-effort removal of the stale screen message before replacing it.

    Needed only for uneditable leftovers — e.g. a plain-text screen saved by an
    older bot version: such a message cannot be converted to a photo by an
    edit, so it must go.
    """
    if uc.screen_message_id is None:
        return
    try:
        await bot.delete_message(chat_id=user.id, message_id=uc.screen_message_id)
    except TelegramBadRequest:
        pass
    await repo.set_screen(session, uc, None)


async def _resolve_media(
    session: AsyncSession,
    cover_id: int,
    banner: Banner,
    photo: str | FSInputFile | None,
    product_id: int | None,
) -> _Media:
    if photo is None:
        return await _banner_media(session, cover_id, banner)
    return _Media(
        photo,
        None,
        cached=isinstance(photo, str),
        product_id=product_id,
    )


async def _send_fresh(
    bot: Bot,
    session: AsyncSession,
    user: User,
    uc: UserCover,
    media: _Media,
    caption: str,
    keyboard: InlineKeyboardMarkup | None,
) -> None:
    """Send a brand-new screen message and remember its id."""
    try:
        sent = await bot.send_photo(
            chat_id=user.id,
            photo=media.source,
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except TelegramBadRequest as exc:
        if not (media.cached and _is_bad_file_id(exc)):
            raise
        # Stale cached file_id — drop it and re-upload from disk.
        await _forget(session, uc.cover_id, media)
        media = await _reupload(session, uc.cover_id, media)
        if media is None:
            raise
        sent = await bot.send_photo(
            chat_id=user.id,
            photo=media.source,
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    await repo.set_screen(session, uc, sent.message_id)
    await _remember(session, uc.cover_id, media, sent)


async def _reupload(session: AsyncSession, cover_id: int, media: _Media) -> _Media | None:
    if media.banner is not None:
        path = media.banner.path_for(cover_id)
        return _Media(FSInputFile(path), media.banner, banner_fingerprint(path))
    if media.product_id is not None:
        from app.services.product_photos import product_photo_path
        path = product_photo_path(media.product_id)
        if path.is_file():
            return _Media(FSInputFile(path), None, product_id=media.product_id)
    return None


async def show_screen(
    bot: Bot,
    session: AsyncSession,
    user: User,
    uc: UserCover,
    banner: Banner | None,
    caption: str,
    keyboard: InlineKeyboardMarkup | None = None,
    force_new: bool = False,
    photo: str | FSInputFile | None = None,
    product_id: int | None = None,
) -> None:
    """Render a screen for the client by editing the existing message when possible.

    With ``force_new`` the screen is posted as a brand-new message at the bottom
    of the chat instead of being edited in place. This is used right after the
    client sends a text message (e.g. /start or an article code): editing a
    message that now sits above the client's text would look unresponsive.

    ``photo`` overrides the banner (a product photo); ``product_id`` makes a
    freshly uploaded product photo cache its file_id for this cover.

    Falls back gracefully: if a cached file_id is stale/invalid it is dropped and
    the banner is re-uploaded from disk; if the message can't be edited at all
    (e.g. a legacy text screen saved by an older bot version) a new one is sent
    and the uneditable one removed.
    """
    if banner is None:
        banner = Banner.WELCOME
    cover_id = uc.cover_id
    media = await _resolve_media(session, cover_id, banner, photo, product_id)

    if force_new:
        await _send_fresh(bot, session, user, uc, media, caption, keyboard)
        return

    if uc.screen_message_id is not None:
        try:
            result = await bot.edit_message_media(
                chat_id=user.id,
                message_id=uc.screen_message_id,
                media=InputMediaPhoto(media=media.source, caption=caption, parse_mode="HTML"),
                reply_markup=keyboard,
            )
            await _remember(session, cover_id, media, result)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            if media.cached and _is_bad_file_id(exc):
                # Stale cached file_id — drop it and retry the edit from disk.
                await _forget(session, cover_id, media)
                fresh = await _reupload(session, cover_id, media)
                if fresh is not None:
                    try:
                        result = await bot.edit_message_media(
                            chat_id=user.id,
                            message_id=uc.screen_message_id,
                            media=InputMediaPhoto(
                                media=fresh.source, caption=caption, parse_mode="HTML"
                            ),
                            reply_markup=keyboard,
                        )
                        await _remember(session, cover_id, fresh, result)
                        return
                    except TelegramBadRequest as retry_exc:
                        logger.info(
                            "Edit retry failed for %s, resending: %s", user.id, retry_exc
                        )
                        media = fresh
            else:
                logger.info("Could not edit screen for %s, resending: %s", user.id, exc)

    # The old screen is of another kind (or uneditable) — replace it entirely.
    await _drop_screen(bot, session, user, uc)
    await _send_fresh(bot, session, user, uc, media, caption, keyboard)
