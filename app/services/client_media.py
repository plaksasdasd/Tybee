"""Files a Telegram client sends into the support dialog (ТЗ-2, block A.2)."""
from __future__ import annotations

from aiogram import Bot
from aiogram.types import Message

from app.support.attachments import TELEGRAM_DOWNLOAD_LIMIT, StoredFile, safe_name

# Not stored: shown to staff only as a marker, as before.
_MARKER_ONLY = ("sticker", "video_note", "animation")


def _stub(kind: str, name: str, reason: str, caption: str) -> str:
    stub = f"[вложение: {kind}, {name}, не загружено: {reason}]"
    return f"{caption}\n{stub}" if caption else stub


def _pick(message: Message) -> tuple[str, str, int | None, str, str] | None:
    """(kind, file_id, size, display name, mime) of the file in ``message``, if any."""
    mid = message.message_id
    if message.photo:
        p = message.photo[-1]  # the largest size
        return "photo", p.file_id, p.file_size, f"photo_{mid}.jpg", "image/jpeg"
    if message.document:
        d = message.document
        mime = d.mime_type or "application/octet-stream"
        return "document", d.file_id, d.file_size, safe_name(d.file_name, f"document_{mid}"), mime
    if message.video:
        v = message.video
        return "video", v.file_id, v.file_size, safe_name(v.file_name, f"video_{mid}.mp4"), v.mime_type or "video/mp4"
    if message.voice:
        v = message.voice
        return "voice", v.file_id, v.file_size, f"voice_{mid}.ogg", v.mime_type or "audio/ogg"
    if message.audio:
        a = message.audio
        return "audio", a.file_id, a.file_size, safe_name(a.file_name, f"audio_{mid}.mp3"), a.mime_type or "audio/mpeg"
    return None


async def collect_telegram_media(bot: Bot, message: Message) -> tuple[str, list[StoredFile]]:
    """Body text and remote Telegram file references, without downloading to disk.

    A file larger than the Bot API download limit (20 MB) becomes a text stub."""
    caption = message.caption or ""
    for kind in _MARKER_ONLY:
        if getattr(message, kind, None) is not None:
            return f"{caption}\n[вложение: {kind}]" if caption else f"[вложение: {kind}]", []
    picked = _pick(message)
    if picked is None:
        return message.text or caption or "[вложение]", []
    kind, file_id, size, name, mime = picked
    if size is not None and size > TELEGRAM_DOWNLOAD_LIMIT:
        return _stub(kind, name, "файл больше 20 МБ", caption), []
    # Keep only the Telegram file_id. The panel downloads the content on demand.
    return caption, [StoredFile(kind=kind, mime=mime, original_name=name, size=size or 0,
                               sha256="", path="", remote_provider="telegram", remote_id=file_id)]
