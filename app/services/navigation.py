"""Single-screen navigation engine using Rich Messages (Bot API 10.1+).

The bot keeps exactly one "screen" message per user. Navigating the menu
*edits* that message instead of deleting it and sending a new one, so the chat
history stays clean.

A screen is always a Rich Message: its body is HTML (``rich_message.html``) with
an optional ``<img src="tg://photo?id=banner">`` block for the banner, while the
inline keyboard stays a normal ``reply_markup``. Because the banner is just a
block inside the rich message, editing it to add or remove the ``<img>`` toggles
the banner in place — the same ``message_id`` shows or hides the picture with no
delete+resend and no invisible placeholder.
"""
from __future__ import annotations

import aiohttp
import json
import logging
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.assets import Banner
from app.db.models import User

logger = logging.getLogger(__name__)

# id used to reference the banner photo inside the rich message html/media
_BANNER_MEDIA_ID = "banner"


def _keyboard_dict(keyboard: InlineKeyboardMarkup | None) -> dict | None:
    return keyboard.model_dump() if keyboard else None


def _build_rich(html: str, image_file_id: str | None, image_path: Path | None) -> dict:
    """Build the ``InputRichMessage`` body for a screen.

    When an image is present (a cached/known file_id, or a local path to upload
    inline) an ``<img>`` block is prepended; otherwise the body is plain text.
    """
    if image_file_id or image_path:
        media_ref = image_file_id if image_file_id else f"attach://{_BANNER_MEDIA_ID}"
        return {
            "html": f'<img src="tg://photo?id={_BANNER_MEDIA_ID}">\n{html}',
            "media": [
                {
                    "id": _BANNER_MEDIA_ID,
                    "media": {"type": "photo", "media": media_ref},
                }
            ],
        }
    return {"html": html}


async def _resolve_image(
    session: AsyncSession, banner: Banner | None, photo: str | None
) -> tuple[str | None, Path | None, str | None]:
    """Return ``(image_file_id, image_path, cache_key)`` for the screen image.

    ``photo`` (an already-uploaded product photo file_id) wins. Otherwise, for a
    banner, a cached file_id is reused; if none is cached yet the local PNG path
    is returned so the caller can upload it inline.
    """
    if photo is not None:
        return photo, None, None
    if banner is not None:
        cached = await repo.get_cached_file_id(session, banner.value)
        if cached:
            return cached, None, None
        return None, banner.path, banner.value
    return None, None, None


async def _cache_from_response(
    session: AsyncSession, cache_key: str | None, result: dict
) -> None:
    """Best-effort extraction of an uploaded banner's file_id for reuse."""
    if not cache_key:
        return
    file_id = None
    if result.get("photo"):
        file_id = result["photo"][-1].get("file_id")
    elif result.get("rich_message", {}).get("blocks"):
        for block in result["rich_message"]["blocks"]:
            if block.get("type") == "photo":
                media = block.get("photo", {}).get("media", {})
                value = media.get("file_id") or media.get("media")
                if isinstance(value, str) and not value.startswith("attach://"):
                    file_id = value
                break
    if file_id:
        await repo.set_cached_file_id(session, cache_key, file_id)


async def _call_rich(
    bot: Bot,
    method: str,
    *,
    chat_id: int,
    message_id: int | None,
    html: str,
    reply_markup: dict | None,
    image_file_id: str | None,
    image_path: Path | None,
) -> dict:
    """Call ``sendRichMessage`` / ``editMessageText`` with a rich body.

    Uploads the banner inline (multipart ``attach://``) the first time it is
    needed; afterwards the cached file_id is referenced directly.
    """
    if image_file_id or image_path:
        rich_message = _build_rich(html, image_file_id, image_path)
    else:
        rich_message = _build_rich(html, None, None)
    if message_id is not None:
        payload["message_id"] = message_id
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    url = bot.session.api.api_url(token=bot.token, method=method)

    if image_path is not None:
        form = aiohttp.FormData()
        for key, value in payload.items():
            form.add_field(key, json.dumps(value), content_type="application/json")
        with open(image_path, "rb") as fh:
            form.add_field(
                _BANNER_MEDIA_ID,
                fh,
                filename=f"{_BANNER_MEDIA_ID}.png",
                content_type="image/png",
            )
            async with aiohttp.ClientSession() as http:
                async with http.post(url, data=form) as resp:
                    data = await resp.json()
    else:
        async with aiohttp.ClientSession() as http:
            async with http.post(url, json=payload) as resp:
                data = await resp.json()

    if not data.get("ok"):
        raise TelegramBadRequest(method=method, message=data.get("description", ""))
    return data["result"]


async def _drop_screen(bot: Bot, session: AsyncSession, user: User) -> None:
    """Best-effort removal of the stale screen message before replacing it.

    Needed only for uneditable leftovers — e.g. a plain-text or photo message
    saved by an older bot version: such a message cannot always be converted to
    a rich one by an edit, so it must go.
    """
    if user.screen_message_id is None:
        return
    try:
        await bot.delete_message(chat_id=user.id, message_id=user.screen_message_id)
    except TelegramBadRequest:
        pass
    await repo.set_screen(session, user, None)


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

    ``banner=None`` renders a screen without a banner (plain text rich message).
    Editing between a banner screen and a banner-less screen toggles the image
    block in place — no delete+resend.

    With ``force_new`` the screen is posted as a brand-new message at the bottom
    of the chat instead of being edited in place. This is used right after the
    user sends a text message (e.g. /start or an article code): editing a message
    that now sits above the user's text would look unresponsive.

    Falls back gracefully: if the message can't be edited at all (e.g. a legacy
    text/photo screen saved by an older bot version) a new one is sent and the
    uneditable one removed.
    """
    image_file_id, image_path, cache_key = await _resolve_image(session, banner, photo)
    markup = _keyboard_dict(keyboard)

    if force_new:
        result = await _call_rich(
            bot,
            "sendRichMessage",
            chat_id=user.id,
            message_id=None,
            html=caption,
            reply_markup=markup,
            image_file_id=image_file_id,
            image_path=image_path,
        )
        await repo.set_screen(session, user, result["message_id"])
        await _cache_from_response(session, cache_key, result)
        return

    if user.screen_message_id is not None:
        try:
            result = await _call_rich(
                bot,
                "editMessageText",
                chat_id=user.id,
                message_id=user.screen_message_id,
                html=caption,
                reply_markup=markup,
                image_file_id=image_file_id,
                image_path=image_path,
            )
            await _cache_from_response(session, cache_key, result)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            logger.info("Could not edit screen for %s, resending: %s", user.id, exc)

    # The old screen is of another kind (or uneditable) — replace it entirely.
    await _drop_screen(bot, session, user)
    result = await _call_rich(
        bot,
        "sendRichMessage",
        chat_id=user.id,
        message_id=None,
        html=caption,
        reply_markup=markup,
        image_file_id=image_file_id,
        image_path=image_path,
    )
    await repo.set_screen(session, user, result["message_id"])
    await _cache_from_response(session, cache_key, result)
