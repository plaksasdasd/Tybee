"""One-time invite links: the invitee signs in via Telegram and their id is filled in automatically."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Staff, StaffInvite, StaffRole
from panel.audit import write_audit
from panel.auth import csrf_guard, require_perm, staff_json, verify_telegram_login, issue_session
from app.login_bot_config import token as login_bot_token
from panel.deps import get_session
from panel.permissions import ROLE_LABELS, Perm, can_manage

INVITE_TTL = timedelta(days=7)
router = APIRouter(prefix="/api", tags=["invites"], dependencies=[Depends(csrf_guard)])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expired(inv: StaffInvite) -> bool:
    exp = inv.expires_at if inv.expires_at.tzinfo else inv.expires_at.replace(tzinfo=timezone.utc)
    return exp <= _now()


def _status(inv: StaffInvite) -> str:
    if inv.used_by is not None:
        return "used"
    if inv.revoked:
        return "revoked"
    return "expired" if _expired(inv) else "active"


def invite_json(inv: StaffInvite) -> dict[str, Any]:
    return {
        "token": inv.token,
        "role": inv.role,
        "role_label": ROLE_LABELS.get(StaffRole(inv.role), inv.role),
        "display_name": inv.display_name,
        "position": inv.position,
        "status": _status(inv),
        "tg_start": f"inv_{inv.token}",
        "expires_at": inv.expires_at.isoformat(),
    }


class InviteCreate(BaseModel):
    role: str
    display_name: str | None = Field(default=None, max_length=128)
    position: str | None = Field(default=None, max_length=128)


@router.post("/staff/invites", status_code=201)
async def create_invite(
    body: InviteCreate,
    actor: Staff = Depends(require_perm(Perm.STAFF_MANAGE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        role = StaffRole(body.role)
    except ValueError:
        raise HTTPException(422, "Неизвестная роль") from None
    if not can_manage(actor, None, role.value):
        raise HTTPException(403, "Вы не можете назначать эту роль")
    inv = StaffInvite(
        token=secrets.token_urlsafe(24),
        role=role.value,
        display_name=(body.display_name or "").strip() or None,
        position=(body.position or "").strip() or None,
        created_by=actor.telegram_id,
        expires_at=_now() + INVITE_TTL,
    )
    session.add(inv)
    await write_audit(session, actor, "staff.invite.create", None, {"role": role.value})
    await session.commit()
    return invite_json(inv)


@router.get("/staff/invites")
async def list_invites(
    actor: Staff = Depends(require_perm(Perm.STAFF_MANAGE)), session: AsyncSession = Depends(get_session)
):
    rows = await session.scalars(select(StaffInvite).order_by(StaffInvite.created_at.desc()).limit(100))
    return [invite_json(i) for i in rows if _status(i) == "active" and can_manage(actor, i.role)]


@router.delete("/staff/invites/{token}")
async def revoke_invite(
    token: str,
    actor: Staff = Depends(require_perm(Perm.STAFF_MANAGE)),
    session: AsyncSession = Depends(get_session),
):
    inv = await session.get(StaffInvite, token)
    if inv is None or not can_manage(actor, inv.role):
        raise HTTPException(404, "Приглашение не найдено")
    inv.revoked = True
    await write_audit(session, actor, "staff.invite.revoke", None, {"role": inv.role})
    await session.commit()
    return {"ok": True}


# ------------------------------------------------------------ public side
async def _valid_invite(session: AsyncSession, token: str) -> StaffInvite:
    inv = await session.get(StaffInvite, token)
    if inv is None:
        raise HTTPException(404, "Приглашение не найдено")
    st = _status(inv)
    if st != "active":
        msg = {"used": "Приглашение уже использовано", "revoked": "Приглашение отозвано", "expired": "Срок приглашения истёк"}
        raise HTTPException(410, msg[st])
    return inv


@router.get("/invites/{token}")
async def invite_info(token: str, session: AsyncSession = Depends(get_session)):
    inv = await _valid_invite(session, token)
    return {"role_label": ROLE_LABELS[StaffRole(inv.role)], "position": inv.position, "display_name": inv.display_name}


async def consume_invite(
    session: AsyncSession, token: str, tg_id: int, name: str | None, username: str | None, photo: str | None
) -> Staff:
    """Turn a valid invite into a Staff row for ``tg_id``. Raises HTTPException with a user-facing detail."""
    inv = await _valid_invite(session, token)
    issuer = await session.scalar(select(Staff).where(Staff.telegram_id == inv.created_by).with_for_update().execution_options(populate_existing=True))
    if issuer is None or not issuer.is_active or not can_manage(issuer, None, inv.role):
        raise HTTPException(410, "Полномочия создателя приглашения прекращены")
    if await session.get(Staff, tg_id) is not None:
        raise HTTPException(409, "Этот аккаунт Telegram уже есть в списке сотрудников")
    result = await session.execute(update(StaffInvite).where(
        StaffInvite.token == token, StaffInvite.used_by.is_(None), StaffInvite.revoked.is_(False),
        StaffInvite.expires_at > _now(),
    ).values(used_by=tg_id, used_at=_now()).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise HTTPException(410, "Приглашение уже использовано или недействительно")
    staff = Staff(
        telegram_id=tg_id,
        display_name=inv.display_name or name or str(tg_id),
        role=inv.role,
        position=inv.position,
        username=username,
        photo_url=photo,
        created_by=inv.created_by,
    )
    session.add(staff)
    await session.flush()
    await write_audit(session, staff, "staff.invite.accept", str(tg_id), {"role": inv.role, "invited_by": inv.created_by})
    await session.commit()
    return staff


@router.post("/invites/{token}/accept")
async def accept_invite(
    token: str,
    payload: dict[str, Any],
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Dev-mode fallback (no login bot configured): the id is typed in by hand."""
    settings = get_settings()
    if "hash" in payload:
        if not verify_telegram_login(payload, login_bot_token()):
            raise HTTPException(401, "Подпись Telegram неверна или устарела")
    elif not (settings.web_dev_login and payload.get("id")):
        raise HTTPException(404, "Not found")
    try:
        tg_id = int(payload["id"])
        if tg_id <= 0:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "Некорректный Telegram ID") from None
    name = " ".join(str(payload.get(k) or "") for k in ("first_name", "last_name")).strip() or None
    staff = await consume_invite(session, token, tg_id, name, payload.get("username"), payload.get("photo_url"))
    await issue_session(request, session, staff, dev="hash" not in payload)
    return staff_json(staff)
