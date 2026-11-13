"""Avito profiles (ТЗ-2, block C.6). Admin only (``avito.manage``).

Secrets never leave this module: ``client_secret`` is stored encrypted and never
returned; the webhook secret is shown once right after subscribing and only its
sha256 is kept. Audit details contain field names, never values of secrets."""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import AvitoChat, AvitoProfile, Staff
from app.services.avito import AvitoClient, AvitoError
from app.services.avito_secrets import AvitoKeyError, decrypt_secret, encrypt_secret, key_configured, webhook_hash
from panel.audit import write_audit
from panel.auth import csrf_guard, require_perm
from panel.deps import get_session
from panel.permissions import Perm

router = APIRouter(prefix="/api/avito", tags=["avito"], dependencies=[Depends(csrf_guard)])
SLUG_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
manage = require_perm(Perm.AVITO_MANAGE)


def make_client(client_id: str, client_secret: str, account_id: int | None) -> AvitoClient:
    """Indirection so tests can replace the HTTP client."""
    return AvitoClient(client_id, client_secret, account_id=account_id)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _json(p: AvitoProfile, clients: int = 0) -> dict[str, Any]:
    return {
        "id": p.id, "slug": p.slug, "title": p.title, "client_id": p.client_id, "account_id": p.account_id,
        "enabled": p.enabled, "last_error": p.last_error, "clients": clients,
        "webhook_subscribed": p.webhook_secret_hash is not None,
        "created_at": _iso(p.created_at), "updated_at": _iso(p.updated_at),
    }


def _encrypt(value: str) -> str:
    try:
        return encrypt_secret(value)
    except AvitoKeyError as exc:
        raise HTTPException(503, str(exc)) from None


async def _get(session: AsyncSession, profile_id: int) -> AvitoProfile:
    profile = await session.get(AvitoProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "Профиль не найден")
    return profile


def _touch(profile: AvitoProfile) -> None:
    # Explicit (sub-second) timestamp: the bot process re-creates its cached client when it changes.
    profile.updated_at = datetime.now(timezone.utc)


@router.get("/profiles")
async def list_profiles(_: Staff = Depends(manage), session: AsyncSession = Depends(get_session)):
    counts = dict(
        (await session.execute(
            select(AvitoChat.profile_id, func.count(func.distinct(AvitoChat.user_id))).group_by(AvitoChat.profile_id)
        )).all()
    )
    profiles = list(await session.scalars(select(AvitoProfile).order_by(AvitoProfile.id)))
    settings = get_settings()
    return {
        "key_configured": key_configured(),
        "webhook_base": settings.avito_webhook_url.rstrip("/") or None,
        "profiles": [_json(p, int(counts.get(p.id, 0))) for p in profiles],
    }


class CreateBody(BaseModel):
    slug: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=128)
    client_id: str = Field(min_length=1, max_length=128)
    client_secret: str = Field(min_length=1, max_length=256)


@router.post("/profiles", status_code=201)
async def create_profile(body: CreateBody, me: Staff = Depends(manage), session: AsyncSession = Depends(get_session)):
    slug = body.slug.strip()
    if not SLUG_RE.match(slug):
        raise HTTPException(422, "slug: допустимы a-z, 0-9, _ и -, длина 1..32")
    if await session.scalar(select(AvitoProfile.id).where(AvitoProfile.slug == slug)):
        raise HTTPException(409, f"Профиль '{slug}' уже существует")
    profile = AvitoProfile(
        slug=slug, title=body.title.strip(), client_id=body.client_id.strip(),
        client_secret_enc=_encrypt(body.client_secret.strip()), enabled=True,
    )
    session.add(profile)
    await session.flush()
    await write_audit(session, me, "avito.profile.create", slug, {"title": profile.title})
    await session.commit()
    return _json(profile)


class UpdateBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    client_id: str | None = Field(default=None, min_length=1, max_length=128)
    client_secret: str | None = Field(default=None, min_length=1, max_length=256)
    enabled: bool | None = None


@router.patch("/profiles/{profile_id}")
async def update_profile(
    profile_id: int, body: UpdateBody, me: Staff = Depends(manage), session: AsyncSession = Depends(get_session)
):
    profile = await _get(session, profile_id)
    changed: list[str] = []
    if body.title is not None and body.title.strip() != profile.title:
        profile.title = body.title.strip()
        changed.append("title")
    if body.client_id is not None and body.client_id.strip() != profile.client_id:
        profile.client_id = body.client_id.strip()
        changed.append("client_id")
    if body.client_secret is not None:
        profile.client_secret_enc = _encrypt(body.client_secret.strip())
        changed.append("client_secret")
    if {"client_id", "client_secret"} & set(changed):
        profile.account_id = None  # new keys may belong to another account: check again
        profile.last_error = None
    if body.enabled is not None and body.enabled != profile.enabled:
        profile.enabled = body.enabled
        changed.append("enabled")
    if changed:
        _touch(profile)
        action = "avito.profile.update"
        if changed == ["enabled"]:
            action = "avito.profile.enable" if profile.enabled else "avito.profile.disable"
        await write_audit(session, me, action, profile.slug, {"fields": changed})
        await session.commit()
    return _json(profile)


def _client(profile: AvitoProfile) -> AvitoClient:
    try:
        secret = decrypt_secret(profile.client_secret_enc)
    except AvitoKeyError as exc:
        raise HTTPException(503, str(exc)) from None
    return make_client(profile.client_id, secret, profile.account_id)


def _short(exc: Exception) -> str:
    # Avito error bodies never contain our secret; trim to keep last_error readable.
    return str(exc)[:500]


@router.post("/profiles/{profile_id}/check")
async def check_profile(profile_id: int, me: Staff = Depends(manage), session: AsyncSession = Depends(get_session)):
    """Get a token and ``/core/v1/accounts/self``; store ``account_id``."""
    profile = await _get(session, profile_id)
    client = _client(profile)
    try:
        data = await client.get_account_self()
    except (AvitoError, OSError, TimeoutError) as exc:
        profile.last_error = f"Проверка не прошла: {_short(exc)}"
        await write_audit(session, me, "avito.profile.check", profile.slug, {"ok": False})
        await session.commit()
        raise HTTPException(422, profile.last_error) from None
    finally:
        await client.close()
    account_id = data.get("id")
    if account_id is None:
        profile.last_error = "Avito не вернул id аккаунта"
        await session.commit()
        raise HTTPException(422, profile.last_error)
    profile.account_id = int(account_id)
    profile.last_error = None
    _touch(profile)
    await write_audit(session, me, "avito.profile.check", profile.slug, {"ok": True, "account_id": profile.account_id})
    await session.commit()
    return {**_json(profile), "account_name": data.get("name")}


@router.post("/profiles/{profile_id}/subscribe")
async def subscribe_webhook(profile_id: int, me: Staff = Depends(manage), session: AsyncSession = Depends(get_session)):
    """New random secret -> Avito ``subscribe`` -> store sha256(secret). The secret is returned once."""
    profile = await _get(session, profile_id)
    base = get_settings().avito_webhook_url.rstrip("/")
    if not base:
        raise HTTPException(422, "AVITO_WEBHOOK_URL не задан: некуда подписывать вебхук")
    secret = secrets.token_urlsafe(32)
    url = f"{base}/avito/webhook/{secret}"
    client = _client(profile)
    try:
        await client.subscribe_webhook(url)
    except (AvitoError, OSError, TimeoutError) as exc:
        profile.last_error = f"Подписка не удалась: {_short(exc)}"
        await write_audit(session, me, "avito.profile.subscribe", profile.slug, {"ok": False})
        await session.commit()
        raise HTTPException(502, profile.last_error) from None
    finally:
        await client.close()
    profile.webhook_secret_hash = webhook_hash(secret)
    profile.last_error = None
    _touch(profile)
    await write_audit(session, me, "avito.profile.subscribe", profile.slug, {"ok": True})
    await session.commit()
    return {**_json(profile), "webhook_secret": secret, "webhook_url": url}
