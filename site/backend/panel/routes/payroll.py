"""Support payroll: monthly calculation, posting and review registry (ТЗ 6.1-6.6)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Staff
from app.finance import notify as fnotify
from app.finance import payroll
from app.finance.common import FinanceError
from panel.audit import write_audit
from panel.auth import csrf_guard, current_staff, require_perm
from panel.deps import get_session
from panel.permissions import Perm, perms_of

router = APIRouter(prefix="/api/finance", tags=["payroll"], dependencies=[Depends(csrf_guard)])

PERIOD = r"^\d{4}-(0[1-9]|1[0-2])$"


def _wrap(exc: FinanceError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def require_any(*perms: Perm):
    needed = set(perms)

    async def dependency(staff: Staff = Depends(current_staff)) -> Staff:
        if not needed & perms_of(staff):
            raise HTTPException(403, "Недостаточно прав")
        return staff

    return dependency


@router.get("/payroll")
async def payroll_report(
    period: str = Query(None, pattern=PERIOD),
    _: Staff = Depends(require_any(Perm.FINANCE_ANALYTICS, Perm.FINANCE_BONUSES)),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await payroll.report(session, period or payroll.current_period())
    except FinanceError as exc:
        raise _wrap(exc) from None


@router.get("/payroll/settings")
async def payroll_settings(
    _: Staff = Depends(require_any(Perm.FINANCE_ANALYTICS, Perm.FINANCE_BONUSES)),
    session: AsyncSession = Depends(get_session),
):
    return await payroll.get_config(session)


class SettingsBody(BaseModel):
    msg_rate_min_kop: int | None = Field(default=None, ge=0, le=1_000_000)
    msg_rate_max_kop: int | None = Field(default=None, ge=0, le=1_000_000)
    client_rate_min_kop: int | None = Field(default=None, ge=0, le=1_000_000)
    client_rate_max_kop: int | None = Field(default=None, ge=0, le=1_000_000)
    order_percent_bp: int | None = Field(default=None, ge=0, le=10000)
    review_bonus_kop: int | None = Field(default=None, ge=0, le=10_000_000)
    min_payout_kop: int | None = Field(default=None, ge=0, le=10_000_000)
    fast_answer_sec: int | None = Field(default=None, ge=1, le=86_400)
    slow_answer_sec: int | None = Field(default=None, ge=1, le=86_400)
    full_answer_chars: int | None = Field(default=None, ge=1, le=10_000)


@router.put("/payroll/settings")
async def update_payroll_settings(
    body: SettingsBody,
    me: Staff = Depends(require_perm(Perm.FINANCE_ANALYTICS)),
    session: AsyncSession = Depends(get_session),
):
    values = {key: value for key, value in body.model_dump().items() if value is not None}
    if not values:
        raise HTTPException(422, "Нет изменений")
    try:
        merged = {**(await payroll.get_config(session)), **values}
        clean = payroll.validate_config(merged)
        saved = await payroll.save_config(session, clean, me.telegram_id)
    except FinanceError as exc:
        raise _wrap(exc) from None
    await write_audit(session, me, "finance.payroll.settings", None, clean)
    await session.commit()
    return saved


class AccrueBody(BaseModel):
    period: str = Field(pattern=PERIOD)
    staff_id: int | None = None


@router.post("/payroll/accrue", status_code=201)
async def accrue_payroll(
    body: AccrueBody,
    me: Staff = Depends(require_perm(Perm.FINANCE_BONUSES)),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await payroll.accrue(
            session, me.telegram_id, body.period, [body.staff_id] if body.staff_id else None
        )
    except FinanceError as exc:
        raise _wrap(exc) from None
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "Начисление за этот месяц уже проведено") from None
    for row in result["created"]:
        await write_audit(
            session, me, "finance.payroll.accrue", str(row["staff_id"]),
            {"period": body.period, "accrual": row["accrual_id"], "total_kop": row["total_kop"]},
        )
    await session.commit()
    for row in result["created"]:
        await fnotify.send(
            [row["staff_id"]],
            f"🧮 <b>Начисление за {body.period}</b>\n"
            f"Зарплата: {fnotify.fmt_rub(row['salary_kop'])}\n"
            f"Премия: {fnotify.fmt_rub(row['bonus_kop'])}\n"
            f"Итого: <b>{fnotify.fmt_rub(row['total_kop'])}</b>",
            "/finance",
        )
    return result


class CancelBody(BaseModel):
    period: str = Field(pattern=PERIOD)


@router.post("/payroll/{staff_id}/cancel")
async def cancel_payroll(
    staff_id: int,
    body: CancelBody,
    me: Staff = Depends(require_perm(Perm.FINANCE_BONUSES)),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await payroll.cancel(session, me.telegram_id, staff_id, body.period)
    except FinanceError as exc:
        raise _wrap(exc) from None
    await write_audit(session, me, "finance.payroll.cancel", str(staff_id), {"period": body.period})
    await session.commit()
    return result


# ----------------------------------------------------------------------- reviews
@router.get("/reviews")
async def list_reviews(
    period: str = Query(None, pattern=PERIOD),
    staff_id: int | None = None,
    _: Staff = Depends(require_any(Perm.FINANCE_ANALYTICS, Perm.FINANCE_BONUSES)),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await payroll.list_reviews(session, period, staff_id)
    except FinanceError as exc:
        raise _wrap(exc) from None


class ReviewBody(BaseModel):
    staff_id: int
    source: str = Field(default="avito", max_length=16)
    rating: int = Field(default=5, ge=1, le=5)
    order_id: int | None = None
    comment: str | None = Field(default=None, max_length=1000)


@router.post("/reviews", status_code=201)
async def add_review(
    body: ReviewBody,
    me: Staff = Depends(require_perm(Perm.FINANCE_BONUSES)),
    session: AsyncSession = Depends(get_session),
):
    try:
        review = await payroll.add_review(
            session, me.telegram_id, staff_id=body.staff_id, source=body.source,
            rating=body.rating, order_id=body.order_id, comment=body.comment,
        )
    except FinanceError as exc:
        raise _wrap(exc) from None
    await write_audit(
        session, me, "finance.review.create", str(body.staff_id),
        {"review": review.id, "source": review.source, "rating": review.rating},
    )
    await session.commit()
    return {"id": review.id}


@router.delete("/reviews/{review_id}", status_code=204)
async def remove_review(
    review_id: int,
    me: Staff = Depends(require_perm(Perm.FINANCE_BONUSES)),
    session: AsyncSession = Depends(get_session),
):
    try:
        await payroll.delete_review(session, review_id)
    except FinanceError as exc:
        raise _wrap(exc) from None
    await write_audit(session, me, "finance.review.delete", str(review_id))
    await session.commit()