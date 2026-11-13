from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.covers import service, spec
from app.covers.service import CoverError
from app.db.models import Cover, Staff
from panel import telegram
from panel.audit import write_audit
from panel.auth import csrf_guard, require_perm
from panel.permissions import Perm
from panel.deps import get_session

admin_only = require_perm(Perm.COVERS_MANAGE)
router = APIRouter(prefix="/api", tags=["covers"], dependencies=[Depends(csrf_guard)])


def cover_json(cover: Cover, stats: dict[str, int] | None = None) -> dict[str, Any]:
    stats = stats or {}
    return {
        "slug": cover.slug,
        "bot_username": cover.bot_username,
        "enabled": cover.enabled,
        "running": service.is_running(cover),
        "last_error": cover.last_error,
        "clients": stats.get("clients", 0),
        "orders": stats.get("orders", 0),
        "created_at": cover.created_at.isoformat() if cover.created_at else None,
    }


async def _cover(session: AsyncSession, slug: str) -> Cover:
    return await service.get_cover(session, slug)


@router.get("/covers")
async def list_covers(_: Staff = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    covers = list(await session.scalars(select(Cover).where(Cover.archived.is_(False)).order_by(Cover.id)))
    stats = await service.cover_stats(session)
    return [cover_json(c, stats.get(c.id)) for c in covers]


class CoverCreate(BaseModel):
    slug: str
    token: str
    brand_name: str = Field(min_length=1)
    template: str = "default"


@router.post("/covers", status_code=201)
async def create_cover(
    body: CoverCreate, staff: Staff = Depends(admin_only), session: AsyncSession = Depends(get_session)
):
    await telegram.fetch_bot_username(body.token.strip())
    cover = await service.create_cover(session, body.slug, body.token, body.brand_name, body.template)
    await write_audit(
        session, staff, "cover.create", cover.slug, {"brand_name": body.brand_name, "template": body.template}
    )
    await session.commit()
    return cover_json(cover)


class CoverPatch(BaseModel):
    enabled: bool


@router.delete("/covers/{slug}", status_code=204)
async def delete_cover(slug: str, staff: Staff = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    cover = await session.scalar(select(Cover).where(Cover.slug == slug, Cover.archived.is_(False)).with_for_update())
    if cover is None:
        raise HTTPException(404, "Обложка не найдена")
    cover.enabled = False
    cover.archived = True
    await write_audit(session, staff, "cover.archive", slug)
    await session.commit()


@router.patch("/covers/{slug}")
async def patch_cover(
    slug: str, body: CoverPatch, staff: Staff = Depends(admin_only), session: AsyncSession = Depends(get_session)
):
    cover = await service.set_enabled(session, slug, body.enabled)
    await write_audit(session, staff, "cover.enable" if body.enabled else "cover.disable", slug)
    await session.commit()
    return cover_json(cover)


@router.get("/covers/{slug}/config")
async def get_config(slug: str, _: Staff = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    cover = await _cover(session, slug)
    return {**service.public_config(cover), "last_error": cover.last_error}


class ConfigPatch(BaseModel):
    brand_name: str | None = None
    unban_fine_rub: int | str | None = None
    texts: dict[str, str] | None = None
    buttons: dict[str, str] | None = None
    statuses: dict[str, str] | None = None
    expected: dict[str, Any] | None = None


@router.put("/covers/{slug}/config")
async def put_config(
    slug: str, body: ConfigPatch, staff: Staff = Depends(admin_only), session: AsyncSession = Depends(get_session)
):
    cover = await session.scalar(select(Cover).where(Cover.slug == slug, Cover.archived.is_(False)).with_for_update())
    if cover is None:
        raise HTTPException(404, "Обложка не найдена")
    patch = body.model_dump(exclude_none=True)
    expected = patch.pop("expected", None)
    if not expected and any(isinstance(patch.get(section), dict) and len(patch[section]) > 1 for section in ("texts", "buttons", "statuses")):
        raise HTTPException(409, "Для массового изменения обновите страницу и повторите")
    if expected:
        current = service.read_config(cover)
        for section, entries in expected.items():
            if isinstance(entries, dict):
                if not isinstance(current.get(section), dict) or any(current[section].get(k) != value for k, value in entries.items()):
                    raise HTTPException(409, "Конфигурация изменилась. Обновите страницу и повторите")
            elif current.get(section) != entries:
                raise HTTPException(409, "Конфигурация изменилась. Обновите страницу и повторите")
    changed = await service.write_config(session, cover, patch)
    if changed:
        await write_audit(session, staff, "cover.config.update", slug, {"changed": changed})
    await session.commit()
    return {"changed": changed}


class TokenBody(BaseModel):
    token: str


@router.put("/covers/{slug}/token")
async def put_token(
    slug: str, body: TokenBody, staff: Staff = Depends(admin_only), session: AsyncSession = Depends(get_session)
):
    cover = await _cover(session, slug)
    token = body.token.strip()
    await telegram.fetch_bot_username(token)
    changed = await service.write_config(session, cover, {"token": token})
    if changed:
        await write_audit(session, staff, "cover.token.update", slug)
    await session.commit()
    return {"changed": changed}


@router.get("/covers/{slug}/banners/{name}")
async def get_banner(
    slug: str, name: str, _: Staff = Depends(admin_only), session: AsyncSession = Depends(get_session)
):
    cover = await _cover(session, slug)
    return FileResponse(
        service.banner_path(cover, name), media_type="image/png", headers={"Cache-Control": "private, max-age=3600"}
    )


@router.put("/covers/{slug}/banners/{name}")
async def put_banner(
    slug: str,
    name: str,
    file: UploadFile = File(...),
    staff: Staff = Depends(admin_only),
    session: AsyncSession = Depends(get_session),
):
    cover = await _cover(session, slug)
    data = await file.read(spec.BANNER_MAX_BYTES + 1)
    changed = service.replace_banner(cover, name, data)
    await write_audit(session, staff, "cover.banner.replace", slug, {"banner": name})
    await session.commit()
    return {"changed": changed, "version": service.banner_version(cover, name)}


@router.get("/covers/{slug}/banners")
async def list_banners(slug: str, _: Staff = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    cover = await _cover(session, slug)
    return [{"name": n, "version": service.banner_version(cover, n)} for n in spec.BANNERS]


@router.get("/spec")
async def get_spec(_: Staff = Depends(admin_only)):
    def section(items):
        return {k: {"placeholders": sorted(v.placeholders), "caption": v.caption} for k, v in items.items()}

    return {
        "texts": section(spec.TEXTS),
        "buttons": section(spec.BUTTONS),
        "statuses": list(spec.STATUSES),
        "banners": list(spec.BANNERS),
        "caption_limit": spec.CAPTION_LIMIT,
        "button_limit": spec.BUTTON_LIMIT,
        "banner_max_bytes": spec.BANNER_MAX_BYTES,
        "shared": spec.SHARED,
    }
