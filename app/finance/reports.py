"""Settlement and revenue reports. Money is kopecks everywhere (order rubles are converted)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AvitoProfile, Cover, FinanceOperation, Order, PayoutRequest, RobokassaPayment, Staff
from app.finance.common import day_range
from app.finance.ledger import RESERVING

_KINDS = ("salary", "bonus", "fine", "adjustment", "payout", "reversal")


async def staff_report(session: AsyncSession, start: date | None, end: date | None) -> dict[str, Any]:
    lo, hi = day_range(start, end)
    period_stmt = select(FinanceOperation.staff_id, FinanceOperation.kind, func.sum(FinanceOperation.amount_kop))
    if lo:
        period_stmt = period_stmt.where(FinanceOperation.created_at >= lo)
    if hi:
        period_stmt = period_stmt.where(FinanceOperation.created_at < hi)
    per: dict[int, dict[str, int]] = defaultdict(lambda: dict.fromkeys(_KINDS, 0))
    for sid, kind, total in (await session.execute(period_stmt.group_by(FinanceOperation.staff_id, FinanceOperation.kind))).all():
        per[sid][kind] = int(total or 0)
    balances = {
        sid: int(t or 0)
        for sid, t in (await session.execute(select(FinanceOperation.staff_id, func.sum(FinanceOperation.amount_kop)).group_by(FinanceOperation.staff_id))).all()
    }
    reserved = {
        sid: int(t or 0)
        for sid, t in (await session.execute(
            select(PayoutRequest.staff_id, func.sum(PayoutRequest.amount_kop))
            .where(PayoutRequest.status.in_(RESERVING)).group_by(PayoutRequest.staff_id)
        )).all()
    }
    ids = set(per) | {s for s, v in balances.items() if v} | set(reserved)
    names = {}
    if ids:
        names = {s.telegram_id: s for s in await session.scalars(select(Staff).where(Staff.telegram_id.in_(ids)))}
    rows = []
    for sid in sorted(ids, key=lambda i: (names[i].display_name.lower() if i in names else "", i)):
        p = per.get(sid) or dict.fromkeys(_KINDS, 0)
        rows.append({
            "staff_id": sid, "name": names[sid].display_name if sid in names else str(sid),
            "role": names[sid].role if sid in names else None,
            "salary": p["salary"], "bonus": p["bonus"], "fine": p["fine"], "adjustment": p["adjustment"] + p["reversal"],
            "paid": -p["payout"], "balance": balances.get(sid, 0), "reserved": reserved.get(sid, 0),
        })
    keys = ("salary", "bonus", "fine", "adjustment", "paid", "balance", "reserved")
    return {"rows": rows, "total": {k: sum(r[k] for r in rows) for k in keys}}


async def revenue_report(
    session: AsyncSession, start: date | None, end: date | None, group_by: str = "day"
) -> dict[str, Any]:
    if group_by not in ("cover", "staff", "day"):
        group_by = "day"
    lo, hi = day_range(start, end)
    orders_stmt = select(Order.paid_at, Order.paid_amount, Order.cover_id, Order.paid_staff_id, Order.avito_profile_id).where(
        Order.paid_at.is_not(None)
    )
    fines_stmt = select(RobokassaPayment.processed_at, RobokassaPayment.amount, RobokassaPayment.cover_id).where(
        RobokassaPayment.status == "paid", RobokassaPayment.processed_at.is_not(None)
    )
    # Refunds count on the day they were made (ТЗ-2, B.3).
    refunds_stmt = select(Order.refunded_at, Order.refund_amount, Order.cover_id, Order.paid_staff_id, Order.avito_profile_id).where(
        Order.refunded_at.is_not(None)
    )
    if lo:
        orders_stmt = orders_stmt.where(Order.paid_at >= lo)
        fines_stmt = fines_stmt.where(RobokassaPayment.processed_at >= lo)
        refunds_stmt = refunds_stmt.where(Order.refunded_at >= lo)
    if hi:
        orders_stmt = orders_stmt.where(Order.paid_at < hi)
        fines_stmt = fines_stmt.where(RobokassaPayment.processed_at < hi)
        refunds_stmt = refunds_stmt.where(Order.refunded_at < hi)
    slugs = dict((await session.execute(select(Cover.id, Cover.slug))).all())
    avito_titles = {
        pid: f"Avito · {title or slug}"
        for pid, title, slug in (await session.execute(select(AvitoProfile.id, AvitoProfile.title, AvitoProfile.slug))).all()
    }
    names = dict((await session.execute(select(Staff.telegram_id, Staff.display_name))).all())

    def key_of(at, cover_id, staff_id, avito_profile_id=None):
        if group_by == "day":
            return at.date().isoformat(), at.date().isoformat()
        if group_by == "cover":
            # Covers and Avito profiles are both sales channels (ТЗ-2, C.6).
            if not cover_id and avito_profile_id:
                return f"avito:{avito_profile_id}", avito_titles.get(avito_profile_id, f"Avito #{avito_profile_id}")
            return (str(cover_id) if cover_id else "-"), (slugs.get(cover_id) or "без обложки")
        return (str(staff_id) if staff_id else "-"), (names.get(staff_id) or ("без сотрудника" if not staff_id else str(staff_id)))

    groups: dict[str, dict[str, Any]] = {}

    def bucket(k, label):
        return groups.setdefault(k, {
            "key": k, "label": label, "orders_count": 0, "orders_sum": 0, "fines_count": 0, "fines_sum": 0,
            "refunds_count": 0, "refunds_sum": 0,
        })

    for at, amount, cover_id, staff_id, avito_id in (await session.execute(orders_stmt)).all():
        b = bucket(*key_of(at, cover_id, staff_id, avito_id))
        b["orders_count"] += 1
        b["orders_sum"] += int(amount or 0) * 100
    for at, amount, cover_id in (await session.execute(fines_stmt)).all():
        b = bucket(*key_of(at, cover_id, None))
        b["fines_count"] += 1
        b["fines_sum"] += int(amount or 0) * 100
    for at, amount, cover_id, staff_id, avito_id in (await session.execute(refunds_stmt)).all():
        b = bucket(*key_of(at, cover_id, staff_id, avito_id))
        b["refunds_count"] += 1
        b["refunds_sum"] += int(amount or 0) * 100
    rows = sorted(groups.values(), key=lambda r: r["key"] if group_by == "day" else r["label"])
    for r in rows:
        r["total"] = r["orders_sum"] + r["fines_sum"]
        r["net"] = r["total"] - r["refunds_sum"]
    keys = ("orders_count", "orders_sum", "fines_count", "fines_sum", "total", "refunds_count", "refunds_sum", "net")
    return {"group_by": group_by, "rows": rows, "total": {k: sum(r[k] for r in rows) for k in keys}}
