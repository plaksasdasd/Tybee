"""Broadcasts (ТЗ-2, D.3). Admin only (``broadcast.send``).

The site only creates rows; the bot process (``app/services/broadcast_worker.py``)
sends them. A test message goes to the admin through the login bot."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from aiogram import Bot
from aiogram.types import BufferedInputFile
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.broadcast import IMAGE_MAX_BYTES, check_body, count_recipients, resolve_cover_ids
from app.catalog.common import CatalogError
from app.config import get_settings
from app.db.models import Broadcast, Cover, Staff
from app.support import attachments as files
from panel.audit import write_audit
from panel.auth import csrf_guard, require_perm
from app.login_bot_config import token as login_bot_token
from panel.deps import get_session
from panel.permissions import Perm

router = APIRouter(prefix="/api/broadcasts", tags=["broadcasts"], dependencies=[Depends(csrf_guard)])
manage = require_perm(Perm.BROADCAST_SEND)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _json(b: Broadcast, slugs: dict[int, str]) -> dict[str, Any]:
    return {
        "id": b.id, "status": b.status, "body": b.body, "has_image": bool(b.image_path),
        "covers": [slugs.get(c, str(c)) for c in (b.cover_ids or [])],
        "total": b.total or 0, "sent": b.sent or 0, "failed": b.failed or 0, "fail_reasons": b.fail_reasons or {},
        "created_by": b.created_by, "created_at": _iso(b.created_at), "started_at": _iso(b.started_at),
        "finished_at": _iso(b.finished_at),
    }


def _cover_ids(raw: str) -> list[int]:
    try:
        return sorted({int(part) for part in (raw or "").replace(";", ",").split(",") if part.strip()})
    except ValueError:
        raise HTTPException(422, "cover_ids: список чисел через запятую") from None


async def _image(image: UploadFile | None) -> tuple[bytes | None, str | None]:
    if image is None or (not image.filename and not image.size):
        return None, None
    data = await image.read(IMAGE_MAX_BYTES + 1)
    if not data:
        return None, None
    if len(data) > IMAGE_MAX_BYTES:
        raise HTTPException(422, "Картинка больше 10 МБ")
    mime = files.validate_image(data)
    if mime is None:
        raise HTTPException(422, "Картинка: допустимы только JPEG, PNG и WebP")
    return data, mime


async def _validate(session: AsyncSession, body: str, cover_ids: str, image: UploadFile | None):
    ids = _cover_ids(cover_ids)
    if ids:
        known = set(await session.scalars(select(Cover.id).where(Cover.id.in_(ids))))
        if known != set(ids):
            raise HTTPException(422, "Неизвестная обложка")
    data, mime = await _image(image)
    try:
        text = check_body(body, with_image=data is not None)
    except CatalogError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return text, ids, data, mime


@router.get("")
async def list_broadcasts(_: Staff = Depends(manage), session: AsyncSession = Depends(get_session)):
    slugs = dict((await session.execute(select(Cover.id, Cover.slug))).all())
    rows = await session.scalars(select(Broadcast).order_by(Broadcast.id.desc()).limit(50))
    return [_json(b, slugs) for b in rows]


@router.get("/covers")
async def covers(_: Staff = Depends(manage), session: AsyncSession = Depends(get_session)):
    """Covers to choose from (ids are needed for ``cover_ids``)."""
    rows = await session.scalars(select(Cover).order_by(Cover.id))
    return [{"id": c.id, "slug": c.slug, "enabled": c.enabled, "bot_username": c.bot_username} for c in rows]


@router.post("/preview")
async def preview(
    body: str = Form(""), cover_ids: str = Form(""), image: UploadFile | None = None,
    _: Staff = Depends(manage), session: AsyncSession = Depends(get_session),
):
    text, ids, data, _mime = await _validate(session, body, cover_ids, image)
    covers = await resolve_cover_ids(session, ids)
    return {"recipients": await count_recipients(session, covers), "covers": len(covers), "has_image": data is not None}


async def send_test_message(chat_id: int, text: str, image: bytes | None) -> None:
    """Through the login bot (the admin has started it to sign in). Replaced in tests."""
    token = login_bot_token()
    if not token:
        raise HTTPException(422, "WEB_LOGIN_BOT_TOKEN не задан: тестовую отправку сделать нельзя")
    bot = Bot(token)
    try:
        if image is not None:
            await bot.send_photo(chat_id, BufferedInputFile(image, "broadcast"), caption=text, parse_mode="HTML")
        else:
            await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception as exc:  # noqa: BLE001 - e.g. HTML rejected by Telegram, bot not started
        detail = getattr(exc, "message", "") or type(exc).__name__
        if "chat not found" in detail or "bot was blocked" in detail:
            detail += " — откройте бота входа в Telegram и нажмите /start"
        elif "parse entities" in detail:
            detail += " — проверьте HTML-разметку текста"
        raise HTTPException(422, f"Telegram не принял сообщение: {detail}") from None
    finally:
        await bot.session.close()


@router.post("/test")
async def test_send(
    body: str = Form(""), cover_ids: str = Form(""), image: UploadFile | None = None,
    me: Staff = Depends(manage), session: AsyncSession = Depends(get_session),
):
    text, _ids, data, _mime = await _validate(session, body, cover_ids, image)
    if me.telegram_id <= 0:
        raise HTTPException(
            422, "Вы вошли через dev-вход без Telegram ID — тест отправить некуда. Войдите через Telegram или задайте WEB_ADMIN_IDS"
        )
    await send_test_message(me.telegram_id, text, data)
    await write_audit(session, me, "broadcast.test", str(me.telegram_id))
    await session.commit()
    return {"ok": True}


@router.post("", status_code=201)
async def create_broadcast(
    body: str = Form(""), cover_ids: str = Form(""), image: UploadFile | None = None,
    me: Staff = Depends(manage), session: AsyncSession = Depends(get_session),
):
    text, ids, data, mime = await _validate(session, body, cover_ids, image)
    covers = await resolve_cover_ids(session, ids)
    total = await count_recipients(session, covers)
    if total == 0:
        raise HTTPException(422, "Нет получателей")
    stored = None
    if data is not None:
        stored = files.store_bytes(data, kind="photo", mime=mime, original_name="broadcast")
    b = Broadcast(
        created_by=me.telegram_id, body=text, image_path=stored.path if stored else None,
        cover_ids=ids, status="queued", total=total, sent=0, failed=0, fail_reasons={},
    )
    session.add(b)
    await session.flush()
    await write_audit(session, me, "broadcast.create", str(b.id), {"covers": ids, "total": total, "image": bool(stored)})
    await session.commit()
    slugs = dict((await session.execute(select(Cover.id, Cover.slug))).all())
    return _json(b, slugs)


@router.post("/{broadcast_id}/cancel")
async def cancel(broadcast_id: int, me: Staff = Depends(manage), session: AsyncSession = Depends(get_session)):
    b = await session.get(Broadcast, broadcast_id)
    if b is None:
        raise HTTPException(404, "Рассылка не найдена")
    if b.status not in ("queued", "running"):
        raise HTTPException(409, "Рассылка уже завершена")
    b.status = "cancelled"
    b.finished_at = datetime.now().astimezone()
    await write_audit(session, me, "broadcast.cancel", str(b.id), {"sent": b.sent, "total": b.total})
    await session.commit()
    return {"ok": True}
