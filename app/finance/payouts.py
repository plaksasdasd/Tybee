"""Payout lifecycle: requested -> approved -> paid (or rejected / cancelled).

Every transition is one ``UPDATE ... WHERE status = <expected>``; losing the race means rowcount 0."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FinanceOperation, PayoutRequest, Staff
from app.finance import ledger, payroll as payroll_rules
from app.finance.common import MAX_KOP, FinanceError

REQUISITES_MAX = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def request(
    session: AsyncSession, staff_id: int, amount_kop: int, comment: str | None, *, allow_below_min: bool = False
) -> PayoutRequest:
    # Row lock serialises concurrent requests of one member (no-op on SQLite).
    staff = await session.scalar(select(Staff).where(Staff.telegram_id == staff_id).with_for_update())
    if staff is None:
        raise FinanceError("Сотрудник не найден", 404)
    requisites = (staff.payout_requisites or "").strip()
    if not requisites:
        raise FinanceError("Сначала заполните реквизиты", 422)
    if amount_kop <= 0 or amount_kop > MAX_KOP:
        raise FinanceError("Сумма должна быть от 0,01 до 10 000 000 ₽", 422)
    minimum = await payroll_rules.min_payout(session)
    if minimum and amount_kop < minimum and not allow_below_min:
        raise FinanceError(
            f"Минимальная сумма выплаты — {payroll_rules.rub(minimum)} ₽. "
            "Меньшие суммы выплачиваются в индивидуальном порядке — обратитесь к финансовому менеджеру",
            422,
        )
    if amount_kop > await ledger.available(session, staff_id):
        raise FinanceError("Сумма больше доступной к выводу", 409)
    req = PayoutRequest(
        staff_id=staff_id, amount_kop=amount_kop, status="requested", requisites=requisites,
        comment=(comment or "").strip() or None,
    )
    session.add(req)
    await session.flush()
    return req


async def _transition(session: AsyncSession, payout_id: int, expected: str, message: str, *conds, **values) -> PayoutRequest:
    result = await session.execute(
        update(PayoutRequest)
        .where(PayoutRequest.id == payout_id, PayoutRequest.status == expected, *conds)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        raise FinanceError(message, 409)
    req = await session.get(PayoutRequest, payout_id, populate_existing=True)
    assert req is not None
    return req


async def _load(session: AsyncSession, payout_id: int) -> PayoutRequest:
    req = await session.get(PayoutRequest, payout_id)
    if req is None:
        raise FinanceError("Запрос не найден", 404)
    return req


async def cancel(session: AsyncSession, payout_id: int, staff_id: int) -> PayoutRequest:
    req = await _load(session, payout_id)
    if req.staff_id != staff_id:
        raise FinanceError("Это чужой запрос", 403)
    return await _transition(
        session, payout_id, "requested", "Отменить можно только запрос в статусе «Запрошено»",
        PayoutRequest.staff_id == staff_id, status="cancelled", decided_at=_now(),
    )


async def _decide(session, payout_id, actor_id, status, comment):
    req = await _load(session, payout_id)
    if req.staff_id == actor_id:
        raise FinanceError("Нельзя решать по собственному запросу", 403)
    return await _transition(
        session, payout_id, "requested", "Запрос уже обработан",
        status=status, decided_by=actor_id, decided_at=_now(), decision_comment=(comment or "").strip() or None,
    )


async def approve(session: AsyncSession, payout_id: int, actor_id: int, comment: str | None = None) -> PayoutRequest:
    return await _decide(session, payout_id, actor_id, "approved", comment)


async def reject(session: AsyncSession, payout_id: int, actor_id: int, reason: str) -> PayoutRequest:
    if not (reason or "").strip():
        raise FinanceError("Укажите причину отказа", 422)
    return await _decide(session, payout_id, actor_id, "rejected", reason)


async def mark_paid(session: AsyncSession, payout_id: int, actor_id: int, reference: str) -> PayoutRequest:
    reference = (reference or "").strip()
    if not reference or len(reference) > 128:
        raise FinanceError("Укажите номер перевода (до 128 символов)", 422)
    req = await _load(session, payout_id)
    if req.staff_id == actor_id:
        raise FinanceError("Нельзя оплачивать собственный запрос", 403)
    req = await _transition(
        session, payout_id, "approved", "Выплатить можно только одобренный запрос",
        status="paid", paid_by=actor_id, paid_at=_now(), paid_reference=reference,
    )
    session.add(
        FinanceOperation(
            staff_id=req.staff_id, kind="payout", amount_kop=-req.amount_kop, payout_id=req.id,
            comment=f"Выплата №{req.id}, перевод {reference}", created_by=actor_id,
        )
    )
    await session.flush()
    return req
