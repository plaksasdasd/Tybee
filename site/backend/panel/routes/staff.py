from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import AuditLog, FinanceOperation, PanelSession, PayoutRequest, Staff, StaffRole
from app.finance.common import FinanceError, rub_to_kop
from app.support.assign import release_threads
from app.support.shift import DEACTIVATED, end_shift
from panel.audit import target_labels, write_audit
from panel.auth import csrf_guard, current_staff, require_perm, staff_json
from panel.deps import get_session
from panel.permissions import ROLE_LABELS, ROLE_PERMS, Perm, can_manage, manageable_roles, perms_of

router = APIRouter(prefix="/api", tags=["staff"], dependencies=[Depends(csrf_guard)])


def member_json(s: Staff, actor: Staff) -> dict[str, Any]:
    out = staff_json(s)
    out.pop("permissions", None)
    out.update(
        is_active=s.is_active,
        created_at=s.created_at.isoformat() if s.created_at else None,
        created_by=s.created_by,
        is_bootstrap=s.telegram_id in get_settings().web_admin_ids,
        manageable=_why_not(actor, s) is None,
        manage_hint=_why_not(actor, s),
    )
    if Perm.STAFF_MANAGE in perms_of(actor) or Perm.FINANCE_ANALYTICS in perms_of(actor):
        out["base_salary_kop"] = int(s.base_salary_kop or 0)
    return out


def _why_not(actor: Staff, target: Staff) -> str | None:
    """Reason ``actor`` cannot edit ``target`` at all (None = may edit)."""
    if Perm.STAFF_MANAGE not in perms_of(actor):
        return "Нет права управлять сотрудниками"
    if actor.telegram_id == target.telegram_id:
        return "Нельзя менять самого себя"
    if target.telegram_id in get_settings().web_admin_ids:
        return "Администратор из WEB_ADMIN_IDS не редактируется"
    if not can_manage(actor, target.role):
        return "Эта роль вам не подчиняется"
    return None


@router.get("/roles")
async def list_roles(actor: Staff = Depends(require_perm(Perm.STAFF_VIEW))):
    assignable = manageable_roles(actor)
    return [
        {
            "code": role.value,
            "label": ROLE_LABELS[role],
            "permissions": sorted(p.value for p in ROLE_PERMS[role]),
            "assignable": role in assignable,
        }
        for role in StaffRole
    ]


@router.get("/staff")
async def list_staff(
    role: str | None = None,
    active: bool | None = None,
    on_shift: bool | None = None,
    q: str | None = None,
    actor: Staff = Depends(require_perm(Perm.STAFF_VIEW)),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Staff).order_by(Staff.created_at, Staff.telegram_id)
    if role:
        stmt = stmt.where(Staff.role == role)
    if active is not None:
        stmt = stmt.where(Staff.is_active == active)
    if on_shift is not None:
        stmt = stmt.where(Staff.on_shift == on_shift)
    if q and q.strip():
        like = f"%{q.strip().lower().lstrip('@')}%"
        cond = [func.lower(Staff.display_name).like(like), func.lower(func.coalesce(Staff.username, "")).like(like)]
        if q.strip().isdigit():
            cond.append(Staff.telegram_id == int(q.strip()))
        stmt = stmt.where(or_(*cond))
    return [member_json(s, actor) for s in await session.scalars(stmt)]


def _parse_role(value: str) -> StaffRole:
    try:
        return StaffRole(value)
    except ValueError:
        raise HTTPException(422, "Неизвестная роль") from None


class StaffCreate(BaseModel):
    telegram_id: int = Field(gt=0)
    display_name: str = Field(min_length=1, max_length=128)
    role: str
    position: str | None = Field(default=None, max_length=128)


@router.post("/staff", status_code=201)
async def create_staff(
    body: StaffCreate,
    actor: Staff = Depends(require_perm(Perm.STAFF_MANAGE)),
    session: AsyncSession = Depends(get_session),
):
    role = _parse_role(body.role)
    if not can_manage(actor, None, role.value):
        raise HTTPException(403, "Вы не можете назначать эту роль")
    if await session.get(Staff, body.telegram_id) is not None:
        raise HTTPException(409, "Сотрудник с таким Telegram ID уже существует")
    if not body.display_name.strip():
        raise HTTPException(422, "Укажите имя сотрудника")
    s = Staff(
        telegram_id=body.telegram_id,
        display_name=body.display_name.strip(),
        role=role.value,
        position=(body.position or "").strip() or None,
        created_by=actor.telegram_id,
    )
    session.add(s)
    await write_audit(session, actor, "staff.create", str(s.telegram_id), {"role": role.value})
    await session.commit()
    await session.refresh(s)
    return member_json(s, actor)


class StaffPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    position: str | None = Field(default=None, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    base_salary: Decimal | None = Field(default=None, ge=0, le=10_000_000)


@router.patch("/staff/{staff_id}")
async def patch_staff(
    staff_id: int,
    body: StaffPatch,
    actor: Staff = Depends(require_perm(Perm.STAFF_MANAGE)),
    session: AsyncSession = Depends(get_session),
):
    s = await session.scalar(select(Staff).where(Staff.telegram_id == staff_id).with_for_update())
    if s is None:
        raise HTTPException(404, "Сотрудник не найден")
    if actor.telegram_id == s.telegram_id:
        raise HTTPException(403, "Нельзя менять самого себя")
    if s.telegram_id in get_settings().web_admin_ids:
        raise HTTPException(409, "Администратор из WEB_ADMIN_IDS не редактируется")
    new_role = _parse_role(body.role) if body.role is not None else None
    if not can_manage(actor, s.role, new_role.value if new_role else None):
        raise HTTPException(403, "Недостаточно прав для этого сотрудника или роли")

    # Never leave the panel without an active admin.
    loses_admin = s.role == StaffRole.ADMIN.value and (
        (new_role is not None and new_role != StaffRole.ADMIN) or body.is_active is False
    )
    if loses_admin and s.is_active:
        admins = await session.scalar(
            select(func.count()).select_from(Staff).where(
                Staff.role == StaffRole.ADMIN.value, Staff.is_active.is_(True), Staff.telegram_id != s.telegram_id
            )
        )
        if not admins:
            raise HTTPException(409, "Должен остаться хотя бы один активный администратор")

    target = str(s.telegram_id)
    fields: list[str] = []
    if new_role is not None and new_role.value != s.role:
        await write_audit(session, actor, "staff.role.change", target, {"from": s.role, "to": new_role.value})
        had_support = Perm.SUPPORT_CHAT in perms_of(s)
        s.role = new_role.value
        if had_support and Perm.SUPPORT_CHAT not in perms_of(s):
            await end_shift(session, s, DEACTIVATED)
            await release_threads(session, s.telegram_id)
    if body.is_active is not None and body.is_active != s.is_active:
        s.is_active = body.is_active
        await write_audit(session, actor, "staff.activate" if body.is_active else "staff.deactivate", target)
        if not body.is_active:
            await session.execute(delete(PanelSession).where(PanelSession.staff_id == s.telegram_id))
            await end_shift(session, s, DEACTIVATED)
            released = await release_threads(session, s.telegram_id)
            if released:
                await write_audit(session, actor, "support.release", target, {"threads": released})
    if body.position is not None:
        pos = body.position.strip() or None
        if pos != s.position:
            s.position = pos
            fields.append("position")
    if body.display_name is not None and not body.display_name.strip():
        raise HTTPException(422, "Укажите имя сотрудника")
    if body.display_name is not None and body.display_name.strip() != s.display_name:
        s.display_name = body.display_name.strip()
        fields.append("display_name")
    if body.base_salary is not None:
        try:
            kop = rub_to_kop(body.base_salary)
        except FinanceError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        if kop != int(s.base_salary_kop or 0):
            await write_audit(session, actor, "staff.salary.change", target, {"from": int(s.base_salary_kop or 0), "to": kop})
            s.base_salary_kop = kop
            fields.append("base_salary_kop")
    if fields:
        await write_audit(session, actor, "staff.update", target, {"fields": fields})
    await session.commit()
    await session.refresh(s)
    return member_json(s, actor)


@router.delete("/staff/{staff_id}", status_code=204)
async def delete_staff(
    staff_id: int,
    actor: Staff = Depends(require_perm(Perm.STAFF_MANAGE)),
    session: AsyncSession = Depends(get_session),
):
    s = await session.scalar(select(Staff).where(Staff.telegram_id == staff_id).with_for_update())
    if s is None:
        raise HTTPException(404, "Сотрудник не найден")
    if actor.telegram_id == s.telegram_id:
        raise HTTPException(403, "Нельзя удалить самого себя")
    if s.telegram_id in get_settings().web_admin_ids:
        raise HTTPException(409, "Администратора из WEB_ADMIN_IDS удалить нельзя")
    if not can_manage(actor, s.role):
        raise HTTPException(403, "Недостаточно прав для этого сотрудника")
    if s.role == StaffRole.ADMIN.value and s.is_active:
        admins = await session.scalar(
            select(func.count()).select_from(Staff).where(
                Staff.role == StaffRole.ADMIN.value, Staff.is_active.is_(True), Staff.telegram_id != s.telegram_id
            )
        )
        if not admins:
            raise HTTPException(409, "Должен остаться хотя бы один активный администратор")
    # The money ledger is append-only and references staff — such people can only be switched off.
    has_money = await session.scalar(
        select(func.count()).select_from(FinanceOperation).where(FinanceOperation.staff_id == s.telegram_id)
    ) or await session.scalar(
        select(func.count()).select_from(PayoutRequest).where(PayoutRequest.staff_id == s.telegram_id)
    )
    if has_money:
        raise HTTPException(409, "У сотрудника есть финансовая история — его можно только отключить")
    target = str(s.telegram_id)
    await end_shift(session, s, DEACTIVATED)
    await session.execute(delete(PanelSession).where(PanelSession.staff_id == s.telegram_id))
    released = await release_threads(session, s.telegram_id)
    if released:
        await write_audit(session, actor, "support.release", target, {"threads": released})
    await write_audit(session, actor, "staff.delete", target, {"name": s.display_name, "role": s.role})
    await session.delete(s)
    await session.commit()


# --------------------------------------------------------------------- audit
def _audit_json(row: AuditLog, names: dict[int, str], targets: dict[int, str]) -> dict[str, Any]:
    return {
        "id": row.id,
        "actor_id": row.actor_id,
        "actor_name": names.get(row.actor_id) if row.actor_id is not None else None,
        "action": row.action,
        "target": row.target,
        "target_label": targets.get(row.id),
        "details": row.details,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def _names(session: AsyncSession, rows: list[AuditLog]) -> dict[int, str]:
    ids = {r.actor_id for r in rows if r.actor_id is not None}
    if not ids:
        return {}
    found = await session.execute(select(Staff.telegram_id, Staff.display_name).where(Staff.telegram_id.in_(ids)))
    return {tg: name for tg, name in found}


@router.get("/audit")
async def list_audit(
    actor: int | None = None,
    action: str | None = None,
    target: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    _: Staff = Depends(require_perm(Perm.AUDIT_VIEW)),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit + 1)
    if actor is not None:
        stmt = stmt.where(AuditLog.actor_id == actor)
    if action:
        stmt = stmt.where(AuditLog.action.startswith(action, autoescape=True))
    if target:
        stmt = stmt.where(AuditLog.target == target)
    if since:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until:
        stmt = stmt.where(AuditLog.created_at <= until)
    if before_id is not None:
        stmt = stmt.where(AuditLog.id < before_id)
    rows = list(await session.scalars(stmt))
    has_more = len(rows) > limit
    rows = rows[:limit]
    names = await _names(session, rows)
    targets = await target_labels(session, rows)
    return {
        "items": [_audit_json(r, names, targets) for r in rows],
        "next_before_id": rows[-1].id if has_more and rows else None,
    }


@router.get("/audit/actions")
async def audit_actions(_: Staff = Depends(require_perm(Perm.AUDIT_VIEW)), session: AsyncSession = Depends(get_session)):
    return list(await session.scalars(select(AuditLog.action).distinct().order_by(AuditLog.action)))


@router.get("/me/cabinet")
async def cabinet(me: Staff = Depends(current_staff), session: AsyncSession = Depends(get_session)):
    rows = list(
        await session.scalars(
            select(AuditLog).where(AuditLog.actor_id == me.telegram_id).order_by(AuditLog.id.desc()).limit(20)
        )
    )
    names = await _names(session, rows)
    targets = await target_labels(session, rows)
    return {
        "profile": staff_json(me),
        "created_at": me.created_at.isoformat() if me.created_at else None,
        "recent_actions": [_audit_json(r, names, targets) for r in rows],
    }
