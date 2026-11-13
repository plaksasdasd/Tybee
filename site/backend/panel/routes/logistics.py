"""Expense requests: accounting only; bank transfers happen outside the panel."""
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LogisticsRequest, Order, Staff
from app.finance.common import FinanceError, MAX_KOP, rub_to_kop
from panel import idempotency
from panel.audit import write_audit
from panel.auth import csrf_guard, require_perm
from panel.deps import get_session
from panel.permissions import Perm, perms_of
from panel.routes.finance import require_any

router = APIRouter(prefix="/api/finance/logistics", tags=["logistics"], dependencies=[Depends(csrf_guard)])


class CreateBody(BaseModel):
    order_id: int | None = Field(default=None, gt=0)
    purpose: str = Field(min_length=1, max_length=256)
    amount: Decimal
    requisites: str = Field(min_length=1, max_length=500)


class DetailBody(BaseModel):
    detail: str = Field(default="", max_length=1000)


@router.get("")
async def list_requests(me: Staff = Depends(require_any(Perm.BUYOUT_MANAGE, Perm.FINANCE_PAYOUTS_APPROVE)), session: AsyncSession = Depends(get_session)):
    stmt = select(LogisticsRequest).order_by(LogisticsRequest.id.desc()).limit(200)
    if Perm.FINANCE_PAYOUTS_APPROVE not in perms_of(me):
        stmt = stmt.where(LogisticsRequest.created_by == me.telegram_id)
    rows = list(await session.scalars(stmt))
    ids = {r.created_by for r in rows} | {r.decided_by for r in rows if r.decided_by is not None} | {r.paid_by for r in rows if r.paid_by is not None}
    names = dict((await session.execute(select(Staff.telegram_id, Staff.display_name).where(Staff.telegram_id.in_(ids)))).all()) if ids else {}
    return [{"id": r.id, "order_id": r.order_id, "purpose": r.purpose, "amount_kop": r.amount_kop,
             "requisites": r.requisites, "status": r.status, "created_by": r.created_by,
             "created_by_name": names.get(r.created_by), "created_at": r.created_at,
             "decided_by_name": names.get(r.decided_by), "decided_at": r.decided_at,
             "decision_comment": r.decision_comment, "paid_by_name": names.get(r.paid_by),
             "paid_at": r.paid_at, "paid_reference": r.paid_reference} for r in rows]


@router.post("", status_code=201)
async def create(body: CreateBody, request: Request, me: Staff = Depends(require_perm(Perm.BUYOUT_MANAGE)), session: AsyncSession = Depends(get_session)):
    purpose, requisites = body.purpose.strip(), body.requisites.strip()
    if not purpose or not requisites:
        raise HTTPException(422, "Укажите назначение и реквизиты")
    try:
        amount = rub_to_kop(body.amount)
    except FinanceError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    if amount <= 0 or amount > MAX_KOP:
        raise HTTPException(422, "Сумма должна быть от 0,01 до 10 000 000 ₽")
    if body.order_id is not None and not await session.get(Order, body.order_id):
        raise HTTPException(404, "Заказ не найден")
    claim, previous = await idempotency.claim(session, request, me.telegram_id, "logistics.create", body.model_dump(mode="json"))
    if previous is not None:
        return {"id": previous}
    row = LogisticsRequest(created_by=me.telegram_id, order_id=body.order_id, purpose=purpose, amount_kop=amount, requisites=requisites)
    session.add(row)
    await session.flush()
    if claim is not None:
        claim.result_id = row.id
    await write_audit(session, me, "logistics.create", str(row.id), {"order_id": row.order_id, "amount_kop": amount})
    await session.commit()
    return {"id": row.id}


@router.post("/{request_id}/{action}")
async def transition(request_id: int, action: str, body: DetailBody, me: Staff = Depends(require_perm(Perm.FINANCE_PAYOUTS_APPROVE)), session: AsyncSession = Depends(get_session)):
    if action not in ("approve", "reject", "paid"):
        raise HTTPException(404, "Действие не найдено")
    detail = body.detail.strip()
    if action in ("reject", "paid") and not detail:
        raise HTTPException(422, "Укажите причину отказа или номер перевода")
    if action == "paid" and len(detail) > 128:
        raise HTTPException(422, "Номер перевода — не более 128 символов")
    row = await session.get(LogisticsRequest, request_id)
    if row is None:
        raise HTTPException(404, "Заявка не найдена")
    if row.created_by == me.telegram_id:
        raise HTTPException(403, "Нельзя обрабатывать собственную заявку")
    now = datetime.now(timezone.utc)
    expected = "approved" if action == "paid" else "requested"
    values = ({"status": "paid", "paid_by": me.telegram_id, "paid_at": now, "paid_reference": detail}
              if action == "paid" else {"status": "approved" if action == "approve" else "rejected", "decided_by": me.telegram_id, "decided_at": now, "decision_comment": detail or None})
    result = await session.execute(update(LogisticsRequest).where(LogisticsRequest.id == request_id, LogisticsRequest.status == expected).values(**values))
    if result.rowcount != 1:
        raise HTTPException(409, "Статус заявки уже изменился")
    await write_audit(session, me, f"logistics.{action}", str(request_id), {"amount_kop": row.amount_kop})
    await session.commit()
    return {"ok": True}
