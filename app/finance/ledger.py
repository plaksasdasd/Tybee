"""Append-only money ledger. Balance = sum of a member's operations."""
from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FinanceOperation, PayoutRequest, Staff
from app.finance.common import MAX_KOP, FinanceError

MANUAL_KINDS = ("salary", "bonus", "fine", "adjustment")
RESERVING = ("requested", "approved")
_PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


async def balance(session: AsyncSession, staff_id: int) -> int:
    return int(
        await session.scalar(
            select(func.coalesce(func.sum(FinanceOperation.amount_kop), 0)).where(FinanceOperation.staff_id == staff_id)
        )
        or 0
    )


async def reserved(session: AsyncSession, staff_id: int) -> int:
    return int(
        await session.scalar(
            select(func.coalesce(func.sum(PayoutRequest.amount_kop), 0)).where(
                PayoutRequest.staff_id == staff_id, PayoutRequest.status.in_(RESERVING)
            )
        )
        or 0
    )


async def available(session: AsyncSession, staff_id: int) -> int:
    return await balance(session, staff_id) - await reserved(session, staff_id)


async def add_operation(
    session: AsyncSession,
    *,
    staff_id: int,
    kind: str,
    amount_kop: int,
    actor_id: int,
    period: str | None = None,
    comment: str | None = None,
) -> FinanceOperation:
    """Manual operation. salary/bonus/fine take a positive magnitude (a fine is stored negative);
    adjustment is signed. Nobody can credit or debit themselves."""
    if kind not in MANUAL_KINDS:
        raise FinanceError("Недопустимый вид операции", 422)
    if staff_id == actor_id:
        raise FinanceError("Нельзя проводить операции по самому себе", 403)
    if await session.get(Staff, staff_id) is None:
        raise FinanceError("Сотрудник не найден", 404)
    if amount_kop == 0 or abs(amount_kop) > MAX_KOP:
        raise FinanceError("Сумма должна быть от 0,01 до 10 000 000 ₽", 422)
    if kind != "adjustment" and amount_kop < 0:
        raise FinanceError("Сумма должна быть положительной", 422)
    comment = (comment or "").strip() or None
    period = (period or "").strip() or None
    if kind == "salary":
        if not period or not _PERIOD.match(period):
            raise FinanceError("Для зарплаты укажите период в формате ГГГГ-ММ", 422)
    else:
        period = period if period and _PERIOD.match(period) else None
    if kind in ("fine", "adjustment") and not comment:
        raise FinanceError("Укажите комментарий", 422)
    op = FinanceOperation(
        staff_id=staff_id, kind=kind, amount_kop=-amount_kop if kind == "fine" else amount_kop,
        period=period, comment=comment, created_by=actor_id,
    )
    session.add(op)
    await session.flush()
    return op


async def reverse(session: AsyncSession, op_id: int, actor_id: int, comment: str) -> FinanceOperation:
    comment = (comment or "").strip()
    if not comment:
        raise FinanceError("Укажите комментарий", 422)
    original = await session.get(FinanceOperation, op_id)
    if original is None:
        raise FinanceError("Операция не найдена", 404)
    if original.staff_id == actor_id:
        raise FinanceError("Нельзя проводить операции по самому себе", 403)
    if original.kind in ("reversal", "payout"):
        raise FinanceError("Эту операцию нельзя сторнировать", 409)
    if await session.scalar(select(FinanceOperation.id).where(FinanceOperation.reversal_of == op_id)):
        raise FinanceError("Операция уже сторнирована", 409)
    rev = FinanceOperation(
        staff_id=original.staff_id, kind="reversal", amount_kop=-original.amount_kop,
        period=original.period, comment=comment, created_by=actor_id, reversal_of=op_id,
    )
    session.add(rev)
    try:
        await session.flush()  # reversal_of is unique: a concurrent second reversal fails here
    except Exception:
        await session.rollback()
        raise FinanceError("Операция уже сторнирована", 409) from None
    return rev
