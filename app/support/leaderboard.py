"""Support leaderboard: what each agent did today, this week and this month.

Read-only numbers for the whole team. They come from the same sources as the payroll
(ТЗ 6.2-6.4), so nobody has to argue about the same facts twice:

* an outgoing message the agent wrote,
* a new client the agent answered first,
* an order paid while the agent was the owner of the thread,
* a confirmed top-mark review,
* a thread the agent closed.

Points are deliberately simple and shown in the panel: a message is 1 point, a new
client 5, a paid order 10, a review 20. They rank people, they are not money.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, Staff, StaffShift, SupportReview, SupportThreadMessage
from app.support.common import now as server_now, ts
from app.timeutil import PERIODS, period_range

WEIGHTS = {"message": 1, "new_client": 5, "order": 10, "review": 20}


async def _messages(session: AsyncSession, ids: list[int], lo: datetime, hi: datetime) -> Counter[int]:
    rows = (
        await session.execute(
            select(SupportThreadMessage.staff_id, func.count())
            .where(
                SupportThreadMessage.direction == "staff",
                SupportThreadMessage.kind == "message",
                SupportThreadMessage.staff_id.in_(ids),
                SupportThreadMessage.created_at >= lo,
                SupportThreadMessage.created_at < hi,
            )
            .group_by(SupportThreadMessage.staff_id)
        )
    ).all()
    return Counter({int(staff_id): int(count or 0) for staff_id, count in rows})


async def _closed(session: AsyncSession, ids: list[int], lo: datetime, hi: datetime) -> Counter[int]:
    rows = (
        await session.execute(
            select(SupportThreadMessage.staff_id, func.count())
            .where(
                SupportThreadMessage.direction == "system",
                SupportThreadMessage.kind == "close",
                SupportThreadMessage.staff_id.in_(ids),
                SupportThreadMessage.created_at >= lo,
                SupportThreadMessage.created_at < hi,
            )
            .group_by(SupportThreadMessage.staff_id)
        )
    ).all()
    return Counter({int(staff_id): int(count or 0) for staff_id, count in rows})


async def _first_replies(
    session: AsyncSession, ids: list[int], lo: datetime, hi: datetime
) -> tuple[Counter[int], dict[int, float | None]]:
    """Threads answered first by the agent inside the period, and the median first answer."""
    initiated = (
        select(SupportThreadMessage.thread_id)
        .where(SupportThreadMessage.direction == "system", SupportThreadMessage.kind == "initiate")
        .scalar_subquery()
    )
    first_staff_message = (
        select(func.min(SupportThreadMessage.id))
        .where(SupportThreadMessage.direction == "staff", SupportThreadMessage.kind == "message")
        .group_by(SupportThreadMessage.thread_id)
        .scalar_subquery()
    )
    first = (
        await session.execute(
            select(
                SupportThreadMessage.staff_id,
                SupportThreadMessage.thread_id,
                SupportThreadMessage.id,
                SupportThreadMessage.created_at,
            ).where(
                SupportThreadMessage.id.in_(first_staff_message),
                SupportThreadMessage.staff_id.in_(ids),
                SupportThreadMessage.created_at >= lo,
                SupportThreadMessage.created_at < hi,
                # Dialogs started by staff are not new clients.
                ~SupportThreadMessage.thread_id.in_(initiated),
            )
        )
    ).all()
    new_clients = Counter({int(row.staff_id): 0 for row in first})
    for row in first:
        new_clients[int(row.staff_id)] += 1
    if not first:
        return new_clients, {}

    thread_ids = {row.thread_id for row in first}
    client_rows = (
        await session.execute(
            select(
                SupportThreadMessage.thread_id,
                SupportThreadMessage.id,
                SupportThreadMessage.created_at,
            ).where(
                SupportThreadMessage.thread_id.in_(thread_ids),
                SupportThreadMessage.direction == "client",
            )
        )
    ).all()
    last_client: dict[int, tuple[int, datetime]] = {}
    for thread_id, message_id, created_at in client_rows:
        known = last_client.get(thread_id)
        if known is None or message_id > known[0]:
            last_client[thread_id] = (message_id, created_at)

    waits: dict[int, list[float]] = defaultdict(list)
    for row in first:
        known = last_client.get(row.thread_id)
        if known is None or known[0] > row.id or row.created_at is None:
            continue
        waits[int(row.staff_id)].append(max(0.0, ts(row.created_at) - ts(known[1])))
    return new_clients, {staff_id: median(values) for staff_id, values in waits.items()}


async def _orders(session: AsyncSession, ids: list[int], lo: datetime, hi: datetime) -> dict[int, tuple[int, int]]:
    rows = (
        await session.execute(
            select(Order.paid_staff_id, func.count(), func.coalesce(func.sum(Order.paid_amount), 0))
            .where(
                Order.paid_staff_id.in_(ids),
                Order.paid_at.is_not(None),
                Order.paid_at >= lo,
                Order.paid_at < hi,
                Order.paid_amount.is_not(None),
                Order.refunded_at.is_(None),
            )
            .group_by(Order.paid_staff_id)
        )
    ).all()
    return {int(staff_id): (int(count or 0), int(rubles or 0) * 100) for staff_id, count, rubles in rows}


async def _reviews(session: AsyncSession, ids: list[int], lo: datetime, hi: datetime) -> Counter[int]:
    rows = (
        await session.execute(
            select(SupportReview.staff_id, func.count())
            .where(
                SupportReview.staff_id.in_(ids),
                SupportReview.confirmed.is_(True),
                SupportReview.rating >= 5,
                SupportReview.created_at >= lo,
                SupportReview.created_at < hi,
            )
            .group_by(SupportReview.staff_id)
        )
    ).all()
    return Counter({int(staff_id): int(count or 0) for staff_id, count in rows})


async def _worked(
    session: AsyncSession, ids: list[int], lo: datetime, hi: datetime
) -> Counter[int]:
    """Seconds on shift inside the period (a running shift counts up to now)."""
    rows = (
        await session.execute(
            select(StaffShift.staff_id, StaffShift.started_at, StaffShift.ended_at).where(
                StaffShift.staff_id.in_(ids),
                StaffShift.started_at < hi,
                (StaffShift.ended_at.is_(None)) | (StaffShift.ended_at > lo),
            )
        )
    ).all()
    worked: Counter[int] = Counter()
    for staff_id, started_at, ended_at in rows:
        begin = max(ts(started_at), ts(lo))
        finish = min(ts(ended_at) if ended_at is not None else ts(hi), ts(hi))
        if finish > begin:
            worked[int(staff_id)] += int(finish - begin)
    return worked


async def build(
    session: AsyncSession, staff: list[Staff], period: str, now: datetime | None = None
) -> dict[str, Any]:
    """Rows for the whole team, best first. ``staff`` is the list allowed to see the board."""
    if period not in PERIODS:
        raise ValueError(period)
    moment = now or server_now()
    lo, hi = period_range(period, moment)
    ids = [member.telegram_id for member in staff]
    empty = {
        "period": period, "from": lo.isoformat(), "to": hi.isoformat(), "weights": WEIGHTS,
        "rows": [], "totals": {"messages": 0, "new_clients": 0, "orders": 0, "reviews": 0, "closed": 0, "worked_sec": 0},
    }
    if not ids:
        return empty

    messages = await _messages(session, ids, lo, hi)
    closed = await _closed(session, ids, lo, hi)
    new_clients, answer_sec = await _first_replies(session, ids, lo, hi)
    orders = await _orders(session, ids, lo, hi)
    reviews = await _reviews(session, ids, lo, hi)
    worked = await _worked(session, ids, lo, hi)

    rows = []
    for member in staff:
        staff_id = member.telegram_id
        order_count, order_kop = orders.get(staff_id, (0, 0))
        score = (
            messages[staff_id] * WEIGHTS["message"]
            + new_clients[staff_id] * WEIGHTS["new_client"]
            + order_count * WEIGHTS["order"]
            + reviews[staff_id] * WEIGHTS["review"]
        )
        rows.append(
            {
                "staff_id": staff_id,
                "name": member.display_name or str(staff_id),
                "role": member.role,
                "on_shift": bool(member.on_shift),
                "score": score,
                "messages": messages[staff_id],
                "new_clients": new_clients[staff_id],
                "orders": order_count,
                "orders_kop": order_kop,
                "reviews": reviews[staff_id],
                "closed": closed[staff_id],
                "answer_sec": round(answer_sec[staff_id], 1) if staff_id in answer_sec else None,
                "worked_sec": worked[staff_id],
            }
        )
    # A quiet member still deserves a place on the board, but only people with any
    # activity make the cut: a name with five zeroes teaches nobody anything.
    rows = [row for row in rows if row["score"] or row["worked_sec"]]
    rows.sort(key=lambda row: (-row["score"], -row["messages"], row["name"]))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return {
        **empty,
        "rows": rows,
        "totals": {
            "messages": sum(row["messages"] for row in rows),
            "new_clients": sum(row["new_clients"] for row in rows),
            "orders": sum(row["orders"] for row in rows),
            "reviews": sum(row["reviews"] for row in rows),
            "closed": sum(row["closed"] for row in rows),
            "worked_sec": sum(row["worked_sec"] for row in rows),
        },
    }