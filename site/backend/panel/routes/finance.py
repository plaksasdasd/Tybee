from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FinanceOperation, Order, PayoutRequest, Staff, SupportThread, Cover, User
from app.finance import ledger, payouts, reports
from app.finance import notify as fnotify
from app.finance.common import FinanceError, day_range, rub_to_kop
from app.support.common import STATUS_LABELS
from panel import idempotency
from panel.audit import write_audit
from panel.auth import csrf_guard, current_staff, require_perm
from panel.deps import get_session
from panel.permissions import Perm, manageable_roles, perms_of

router = APIRouter(prefix="/api", tags=["finance"], dependencies=[Depends(csrf_guard)])

PAGE = 100
_KIND_LABELS = {
    "salary": "Зарплата", "bonus": "Премия", "fine": "Штраф", "adjustment": "Корректировка",
    "payout": "Выплата", "reversal": "Отмена операции",
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _wrap(exc: FinanceError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def require_any(*perms: Perm):
    needed = set(perms)

    async def dependency(staff: Staff = Depends(current_staff)) -> Staff:
        if not needed & perms_of(staff):
            raise HTTPException(403, "Недостаточно прав")
        return staff

    return dependency


async def _target(session: AsyncSession, me: Staff, staff_id: int) -> Staff:
    """Member that ``me`` may post money operations for."""
    target = await session.get(Staff, staff_id)
    if target is None:
        raise HTTPException(404, "Сотрудник не найден")
    if target.telegram_id == me.telegram_id:
        raise HTTPException(403, "Нельзя проводить операции по самому себе")
    if not any(r.value == target.role for r in manageable_roles(me)):
        raise HTTPException(403, "Этим сотрудником вы не управляете")
    return target


# ---------------------------------------------------------------- operations
def _op_json(op: FinanceOperation, names: dict[int, str], reversed_ids: set[int]) -> dict[str, Any]:
    return {
        "id": op.id, "staff_id": op.staff_id, "staff_name": names.get(op.staff_id, str(op.staff_id)),
        "kind": op.kind, "kind_label": _KIND_LABELS.get(op.kind, op.kind), "amount_kop": op.amount_kop,
        "period": op.period, "comment": op.comment, "created_by": op.created_by,
        "created_by_name": names.get(op.created_by) if op.created_by else None,
        "payout_id": op.payout_id, "reversal_of": op.reversal_of,
        "reversed": op.id in reversed_ids, "created_at": _iso(op.created_at),
    }


async def _names(session: AsyncSession, ids: set[int | None]) -> dict[int, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return dict((await session.execute(select(Staff.telegram_id, Staff.display_name).where(Staff.telegram_id.in_(ids)))).all())


def _ops_stmt(me: Staff, staff_id, kind, start, end):
    stmt = select(FinanceOperation)
    if Perm.FINANCE_ANALYTICS not in perms_of(me):
        roles = [r.value for r in manageable_roles(me)]
        stmt = stmt.join(Staff, Staff.telegram_id == FinanceOperation.staff_id).where(Staff.role.in_(roles))
    if staff_id:
        stmt = stmt.where(FinanceOperation.staff_id == staff_id)
    if kind:
        stmt = stmt.where(FinanceOperation.kind == kind)
    lo, hi = day_range(start, end)
    if lo:
        stmt = stmt.where(FinanceOperation.created_at >= lo)
    if hi:
        stmt = stmt.where(FinanceOperation.created_at < hi)
    return stmt


async def _ops_rows(session: AsyncSession, ops: list[FinanceOperation]) -> list[dict[str, Any]]:
    names = await _names(session, {o.staff_id for o in ops} | {o.created_by for o in ops})
    rev = set()
    if ops:
        rev = set(await session.scalars(select(FinanceOperation.reversal_of).where(FinanceOperation.reversal_of.in_([o.id for o in ops]))))
    return [_op_json(o, names, rev) for o in ops]


@router.get("/finance/operations")
async def list_operations(
    staff_id: int | None = None,
    kind: str | None = Query(None, pattern="^(salary|bonus|fine|adjustment|payout|reversal)$"),
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
    before_id: int | None = None,
    me: Staff = Depends(require_any(Perm.FINANCE_ANALYTICS, Perm.FINANCE_BONUSES)),
    session: AsyncSession = Depends(get_session),
):
    stmt = _ops_stmt(me, staff_id, kind, start, end)
    if before_id:
        stmt = stmt.where(FinanceOperation.id < before_id)
    ops = list(await session.scalars(stmt.order_by(FinanceOperation.id.desc()).limit(PAGE + 1)))
    more = len(ops) > PAGE
    ops = ops[:PAGE]
    return {"items": await _ops_rows(session, ops), "next_before_id": ops[-1].id if more else None}


class OperationBody(BaseModel):
    staff_id: int
    kind: str = Field(pattern="^(salary|bonus|fine|adjustment)$")
    amount: Decimal
    period: str | None = Field(default=None, max_length=7)
    comment: str | None = Field(default=None, max_length=1000)


@router.post("/finance/operations", status_code=201)
async def create_operation(
    body: OperationBody, request: Request, me: Staff = Depends(require_perm(Perm.FINANCE_BONUSES)), session: AsyncSession = Depends(get_session)
):
    await _target(session, me, body.staff_id)
    claim, previous = await idempotency.claim(session, request, me.telegram_id, "finance.operation", body.model_dump(mode="json"))
    if previous is not None:
        return {"id": previous}
    try:
        op = await ledger.add_operation(
            session, staff_id=body.staff_id, kind=body.kind, amount_kop=rub_to_kop(body.amount),
            actor_id=me.telegram_id, period=body.period, comment=body.comment,
        )
    except FinanceError as exc:
        raise _wrap(exc) from None
    if claim is not None:
        claim.result_id = op.id
    await write_audit(session, me, "finance.op.create", str(op.id), {"kind": op.kind, "amount_kop": op.amount_kop, "staff": op.staff_id})
    await session.commit()
    await fnotify.send(
        [op.staff_id],
        f"💰 <b>{_KIND_LABELS[op.kind]}</b>: {fnotify.fmt_rub(op.amount_kop)}"
        + (f"\n{fnotify.esc(op.comment)}" if op.comment else "")
        + (f"\nПериод: {op.period}" if op.period else ""),
        "/me",
    )
    return {"id": op.id}


class ReverseBody(BaseModel):
    comment: str = Field(min_length=1, max_length=1000)


@router.post("/finance/operations/{op_id}/reverse", status_code=201)
async def reverse_operation(
    op_id: int, body: ReverseBody, me: Staff = Depends(require_perm(Perm.FINANCE_BONUSES)), session: AsyncSession = Depends(get_session)
):
    original = await session.get(FinanceOperation, op_id)
    if original is None:
        raise HTTPException(404, "Операция не найдена")
    await _target(session, me, original.staff_id)
    try:
        rev = await ledger.reverse(session, op_id, me.telegram_id, body.comment)
    except FinanceError as exc:
        raise _wrap(exc) from None
    await write_audit(session, me, "finance.op.reverse", str(op_id), {"reversal": rev.id, "staff": rev.staff_id})
    await session.commit()
    await fnotify.send(
        [rev.staff_id], f"↩️ <b>Отмена операции</b> №{op_id}: {fnotify.fmt_rub(rev.amount_kop)}\n{fnotify.esc(rev.comment)}", "/me"
    )
    return {"id": rev.id}


# ---------------------------------------------------------------- payouts queue
def _payout_json(p: PayoutRequest, names: dict[int, str], *, requisites: bool) -> dict[str, Any]:
    return {
        "id": p.id, "staff_id": p.staff_id, "staff_name": names.get(p.staff_id, str(p.staff_id)),
        "amount_kop": p.amount_kop, "status": p.status, "comment": p.comment,
        "requisites": p.requisites if requisites else None,
        "decided_by_name": names.get(p.decided_by) if p.decided_by else None, "decided_at": _iso(p.decided_at),
        "decision_comment": p.decision_comment, "paid_by_name": names.get(p.paid_by) if p.paid_by else None,
        "paid_at": _iso(p.paid_at), "paid_reference": p.paid_reference, "created_at": _iso(p.created_at),
    }


@router.get("/finance/payouts")
async def list_payouts(
    status: str | None = Query(None, pattern="^(requested|approved|rejected|paid|cancelled)$"),
    me: Staff = Depends(require_any(Perm.FINANCE_PAYOUTS_APPROVE, Perm.FINANCE_PAYOUTS_PAY)),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(PayoutRequest).order_by(PayoutRequest.id.desc()).limit(200)
    if Perm.FINANCE_PAYOUTS_APPROVE not in perms_of(me):
        roles = [r.value for r in manageable_roles(me)]
        stmt = stmt.join(Staff, Staff.telegram_id == PayoutRequest.staff_id).where(Staff.role.in_(roles), Staff.telegram_id != me.telegram_id)
    if status:
        stmt = stmt.where(PayoutRequest.status == status)
    rows = list(await session.scalars(stmt))
    names = await _names(session, {r.staff_id for r in rows} | {r.decided_by for r in rows} | {r.paid_by for r in rows})
    can_approve = Perm.FINANCE_PAYOUTS_APPROVE in perms_of(me)
    return [_payout_json(r, names, requisites=can_approve or r.status == "approved") for r in rows]


@router.get("/finance/summary")
async def finance_summary(
    _: Staff = Depends(require_perm(Perm.FINANCE_PAYOUTS_APPROVE)), session: AsyncSession = Depends(get_session)
):
    n = await session.scalar(select(func.count()).select_from(PayoutRequest).where(PayoutRequest.status == "requested"))
    return {"requested": int(n or 0)}


class DecisionBody(BaseModel):
    comment: str | None = Field(default=None, max_length=1000)


class RejectBody(BaseModel):
    comment: str = Field(min_length=1, max_length=1000)


class PaidBody(BaseModel):
    reference: str = Field(min_length=1, max_length=128)


async def _decision(session, me, payout_id, action, text_for, call, audit_extra=None):
    try:
        req = await call()
    except FinanceError as exc:
        raise _wrap(exc) from None
    await write_audit(session, me, f"finance.payout.{action}", str(payout_id), {"staff": req.staff_id, "amount_kop": req.amount_kop, **(audit_extra or {})})
    await session.commit()
    await fnotify.send([req.staff_id], text_for(req), "/me")
    return {"ok": True, "status": req.status}


@router.post("/finance/payouts/{payout_id}/approve")
async def approve_payout(
    payout_id: int, body: DecisionBody | None = None,
    me: Staff = Depends(require_perm(Perm.FINANCE_PAYOUTS_APPROVE)), session: AsyncSession = Depends(get_session),
):
    return await _decision(
        session, me, payout_id, "approve",
        lambda r: f"✅ Выплата №{r.id} на {fnotify.fmt_rub(r.amount_kop)} одобрена",
        lambda: payouts.approve(session, payout_id, me.telegram_id, body.comment if body else None),
    )


@router.post("/finance/payouts/{payout_id}/reject")
async def reject_payout(
    payout_id: int, body: RejectBody,
    me: Staff = Depends(require_perm(Perm.FINANCE_PAYOUTS_APPROVE)), session: AsyncSession = Depends(get_session),
):
    return await _decision(
        session, me, payout_id, "reject",
        lambda r: f"❌ Выплата №{r.id} на {fnotify.fmt_rub(r.amount_kop)} отклонена\n{fnotify.esc(r.decision_comment)}",
        lambda: payouts.reject(session, payout_id, me.telegram_id, body.comment),
    )


@router.post("/finance/payouts/{payout_id}/paid")
async def paid_payout(
    payout_id: int, body: PaidBody,
    me: Staff = Depends(require_perm(Perm.FINANCE_PAYOUTS_PAY)), session: AsyncSession = Depends(get_session),
):
    if Perm.FINANCE_PAYOUTS_APPROVE not in perms_of(me):
        payout = await session.get(PayoutRequest, payout_id)
        if payout is None:
            raise HTTPException(404, "Запрос не найден")
        await _target(session, me, payout.staff_id)
    return await _decision(
        session, me, payout_id, "paid",
        lambda r: f"💸 Выплата №{r.id} на {fnotify.fmt_rub(r.amount_kop)} выполнена\nПеревод: {fnotify.esc(r.paid_reference)}",
        lambda: payouts.mark_paid(session, payout_id, me.telegram_id, body.reference),
        {"reference": body.reference.strip()},
    )


# ---------------------------------------------------------------- reports & CSV
@router.get("/finance/reports/staff")
async def report_staff(
    start: date | None = Query(None, alias="from"), end: date | None = Query(None, alias="to"),
    _: Staff = Depends(require_perm(Perm.FINANCE_ANALYTICS)), session: AsyncSession = Depends(get_session),
):
    return await reports.staff_report(session, start, end)


@router.get("/finance/reports/revenue")
async def report_revenue(
    start: date | None = Query(None, alias="from"), end: date | None = Query(None, alias="to"),
    group_by: str = Query("day", pattern="^(cover|staff|day)$"),
    _: Staff = Depends(require_perm(Perm.FINANCE_ANALYTICS)), session: AsyncSession = Depends(get_session),
):
    return await reports.revenue_report(session, start, end, group_by)


def csv_cell(value: Any) -> str:
    """Excel-friendly cell. Text starting with = + - @ is neutralised (CSV injection); numbers are not text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (int, float, Decimal)):
        return str(value).replace(".", ",")
    text = str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def csv_response(filename: str, header: list[str], rows: list[list[Any]]) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow([csv_cell(h) for h in header])
    for row in rows:
        writer.writerow([csv_cell(c) for c in row])
    return Response(
        content=("﻿" + buf.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _rub(kop: int) -> Decimal:
    return Decimal(kop) / 100


@router.get("/finance/export/{report}.csv")
@router.get("/finance/export/{report}.xlsx")
async def export_csv(
    report: str,
    request: Request,
    staff_id: int | None = None,
    kind: str | None = Query(None, pattern="^(salary|bonus|fine|adjustment|payout|reversal)$"),
    start: date | None = Query(None, alias="from"), end: date | None = Query(None, alias="to"),
    group_by: str = Query("day", pattern="^(cover|staff|day)$"),
    me: Staff = Depends(require_perm(Perm.FINANCE_ANALYTICS)), session: AsyncSession = Depends(get_session),
):
    def export_response(filename: str, header: list[str], rows: list[list[Any]]) -> Response:
        if not request.url.path.endswith(".xlsx"):
            return csv_response(filename, header, rows)
        from openpyxl import Workbook
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("Отчёт")
        sheet.append(header)
        for row in rows:
            # Force user-controlled strings to be literal text, never formulas.
            from openpyxl.cell import WriteOnlyCell
            cells = []
            for value in row:
                if isinstance(value, str):
                    cell = WriteOnlyCell(sheet, value=value)
                    cell.data_type = "s"
                    cells.append(cell)
                else:
                    cells.append(value)
            sheet.append(cells)
        buf = io.BytesIO()
        workbook.save(buf)
        name = filename.rsplit(".", 1)[0] + ".xlsx"
        return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{name}"'})
    if report == "operations":
        ops = list(await session.scalars(_ops_stmt(me, staff_id, kind, start, end).order_by(FinanceOperation.id.desc()).limit(50_000)))
        rows = await _ops_rows(session, ops)
        response = export_response(
            "operations.csv", ["ID", "Дата", "Сотрудник", "Вид", "Сумма, ₽", "Период", "Комментарий", "Кто провёл"],
            [[r["id"], r["created_at"], r["staff_name"], r["kind_label"], _rub(r["amount_kop"]), r["period"], r["comment"], r["created_by_name"]] for r in rows],
        )
    elif report == "staff":
        data = await reports.staff_report(session, start, end)
        response = export_response(
            "staff.csv", ["Сотрудник", "Зарплата, ₽", "Премии, ₽", "Штрафы, ₽", "Корректировки, ₽", "Выплачено, ₽", "Баланс (долг), ₽", "В резерве, ₽"],
            [[r["name"], *[_rub(r[k]) for k in ("salary", "bonus", "fine", "adjustment", "paid", "balance", "reserved")]] for r in data["rows"]]
            + [["Итого", *[_rub(data["total"][k]) for k in ("salary", "bonus", "fine", "adjustment", "paid", "balance", "reserved")]]],
        )
    elif report == "revenue":
        data = await reports.revenue_report(session, start, end, group_by)
        response = export_response(
            "revenue.csv",
            ["Группа", "Оплат заказов", "Заказы, ₽", "Штрафов за разбан", "Штрафы, ₽", "Итого, ₽", "Возвратов", "Возвраты, ₽", "Чистая выручка, ₽"],
            [[r["label"], r["orders_count"], _rub(r["orders_sum"]), r["fines_count"], _rub(r["fines_sum"]), _rub(r["total"]),
              r["refunds_count"], _rub(r["refunds_sum"]), _rub(r["net"])] for r in data["rows"]]
            + [["Итого", data["total"]["orders_count"], _rub(data["total"]["orders_sum"]), data["total"]["fines_count"],
                _rub(data["total"]["fines_sum"]), _rub(data["total"]["total"]), data["total"]["refunds_count"],
                _rub(data["total"]["refunds_sum"]), _rub(data["total"]["net"])]],
        )
    else:
        raise HTTPException(404, "Неизвестный отчёт")
    await write_audit(session, me, "finance.export", report)
    await session.commit()
    return response


# ---------------------------------------------------------------- my cabinet
@router.get("/me/finance")
async def my_finance(me: Staff = Depends(current_staff), session: AsyncSession = Depends(get_session)):
    bal = await ledger.balance(session, me.telegram_id)
    res = await ledger.reserved(session, me.telegram_id)
    ops = list(await session.scalars(
        select(FinanceOperation).where(FinanceOperation.staff_id == me.telegram_id).order_by(FinanceOperation.id.desc()).limit(100)
    ))
    reqs = list(await session.scalars(
        select(PayoutRequest).where(PayoutRequest.staff_id == me.telegram_id).order_by(PayoutRequest.id.desc()).limit(50)
    ))
    names = await _names(session, {o.created_by for o in ops} | {me.telegram_id})
    return {
        "balance_kop": bal, "reserved_kop": res, "available_kop": bal - res,
        "requisites": me.payout_requisites or "", "has_requisites": bool((me.payout_requisites or "").strip()),
        "operations": [_op_json(o, names, set()) for o in ops],
        "payouts": [_payout_json(r, names, requisites=True) for r in reqs],
    }


class RequisitesBody(BaseModel):
    requisites: str = Field(max_length=500)


@router.put("/me/requisites")
async def set_requisites(body: RequisitesBody, me: Staff = Depends(current_staff), session: AsyncSession = Depends(get_session)):
    me.payout_requisites = body.requisites.strip() or None
    await write_audit(session, me, "finance.requisites.update", str(me.telegram_id))  # never the value
    await session.commit()
    return {"has_requisites": bool(me.payout_requisites)}


class PayoutBody(BaseModel):
    amount: Decimal
    comment: str | None = Field(default=None, max_length=1000)


@router.post("/me/payouts", status_code=201)
async def request_payout(body: PayoutBody, me: Staff = Depends(current_staff), session: AsyncSession = Depends(get_session)):
    try:
        req = await payouts.request(
            session, me.telegram_id, rub_to_kop(body.amount), body.comment,
            allow_below_min=Perm.FINANCE_PAYOUTS_APPROVE in perms_of(me),
        )
    except FinanceError as exc:
        raise _wrap(exc) from None
    await write_audit(session, me, "finance.payout.request", str(req.id), {"amount_kop": req.amount_kop})
    approver_ids = await fnotify.approvers(session, exclude=me.telegram_id)
    await session.commit()
    await fnotify.send(
        approver_ids, f"🧾 <b>Запрос на выплату</b> №{req.id}\n{fnotify.esc(me.display_name)}: {fnotify.fmt_rub(req.amount_kop)}", "/finance"
    )
    return {"id": req.id}


@router.post("/me/payouts/{payout_id}/cancel")
async def cancel_payout(payout_id: int, me: Staff = Depends(current_staff), session: AsyncSession = Depends(get_session)):
    try:
        req = await payouts.cancel(session, payout_id, me.telegram_id)
    except FinanceError as exc:
        raise _wrap(exc) from None
    await write_audit(session, me, "finance.payout.cancel", str(payout_id), {"amount_kop": req.amount_kop})
    await session.commit()
    return {"ok": True}


@router.get("/me/threads")
async def my_threads(me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)):
    threads = list(await session.scalars(
        select(SupportThread).where(SupportThread.assigned_to == me.telegram_id, SupportThread.status == "open")
        .order_by(SupportThread.last_message_at.desc().nulls_last(), SupportThread.id.desc()).limit(200)
    ))
    if not threads:
        return []
    uids = {t.user_id for t in threads}
    users = {u.id: u for u in await session.scalars(select(User).where(User.id.in_(uids)))}
    slugs = dict((await session.execute(select(Cover.id, Cover.slug))).all())
    orders: dict[int, list[Order]] = {}
    for o in await session.scalars(select(Order).where(Order.user_id.in_(uids)).order_by(Order.created_at.desc(), Order.id.desc())):
        orders.setdefault(o.user_id, []).append(o)
    return [
        {
            "id": t.id, "unread": t.unread or 0, "cover": slugs.get(t.last_cover_id),
            "client": {"id": t.user_id, "name": (users[t.user_id].full_name if t.user_id in users else "") or str(t.user_id),
                       "username": users[t.user_id].username if t.user_id in users else None},
            "orders": [
                {"id": o.id, "title": o.title, "price": o.price, "status": o.status, "status_label": STATUS_LABELS.get(o.status, o.status)}
                for o in orders.get(t.user_id, [])[:10]
            ],
        }
        for t in threads
    ]
