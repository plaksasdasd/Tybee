"""Support payroll (ТЗ 6.1-6.6).

Three parts of the reward: the fixed salary, the dialogue pay and the successful-order
premium, plus one-off review bonuses. The period is a calendar month (Moscow time).

Rates live in ``payroll_settings`` (one row) and are editable from the panel:

* per outgoing message   0,50 - 1,00 ₽ (dynamic)
* per accepted new client 3,00 - 5,00 ₽ (dynamic)
* order premium 1,5 % - 2,5 % (default 2,5 %)
* review on the top mark 100 ₽
* minimum payout 1 500 ₽

The dynamic part is a quality coefficient of the member for the month: how fast
the first answer went out and how full the answers were. Both halves weigh 0,5.

Money is kopecks everywhere; order prices stay rubles and are converted on the way.
Nothing here posts money on its own: ``accrue`` writes the usual ledger rows, so a
mistake is fixed by the existing reversal, never by an UPDATE.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    FinanceOperation,
    Order,
    PayrollAccrual,
    PayrollSetting,
    Staff,
    SupportReview,
    SupportThreadMessage,
)
from app.finance import ledger
from app.finance.common import FinanceError
from app.timeutil import MSK

SUPPORT_ROLES = (
    "client_manager_1",
    "client_manager_2",
    "support_lead",
    "arbitration_agent",
    "buyout_agent",
    "admin",
)

DEFAULTS: dict[str, int] = {
    "msg_rate_min_kop": 50,
    "msg_rate_max_kop": 100,
    "client_rate_min_kop": 300,
    "client_rate_max_kop": 500,
    "order_percent_bp": 250,
    "review_bonus_kop": 10000,
    "min_payout_kop": 150000,
    "fast_answer_sec": 120,
    "slow_answer_sec": 1800,
    "full_answer_chars": 200,
}

REVIEW_SOURCES = {"avito": "Avito", "telegram": "Telegram", "yandex": "Яндекс"}


def period_range(period: str) -> tuple[datetime, datetime]:
    """``YYYY-MM`` -> half-open UTC range of that Moscow calendar month."""
    try:
        year, month = (int(part) for part in period.split("-"))
        if not 1 <= month <= 12:
            raise ValueError
        start = datetime(year, month, 1, tzinfo=MSK)
    except (ValueError, TypeError):
        raise FinanceError("Период указывается в формате ГГГГ-ММ", 422) from None
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=MSK)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def current_period() -> str:
    return datetime.now(MSK).strftime("%Y-%m")


def rub(kop: int) -> str:
    sign = "−" if kop < 0 else ""
    whole, rest = divmod(abs(kop), 100)
    text = f"{whole:,}".replace(",", " ")
    return f"{sign}{text},{rest:02d}" if rest else f"{sign}{text}"


def _awake(value: datetime) -> datetime:
    """SQLite returns naive timestamps; the app stores UTC everywhere."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


# --------------------------------------------------------------------- settings
async def get_config(session: AsyncSession) -> dict[str, int]:
    row = await session.get(PayrollSetting, 1)
    if row is None:
        return dict(DEFAULTS)
    return {key: int(getattr(row, key) or 0) for key in DEFAULTS}


async def save_config(session: AsyncSession, values: dict[str, int], actor_id: int) -> dict[str, int]:
    row = await session.get(PayrollSetting, 1)
    if row is None:
        row = PayrollSetting(id=1)
        session.add(row)
    for key, value in values.items():
        if key in DEFAULTS:
            setattr(row, key, int(value))
    row.updated_by = actor_id
    await session.flush()
    return {key: int(getattr(row, key) or 0) for key in DEFAULTS}


def validate_config(values: dict[str, int]) -> dict[str, int]:
    clean = {key: int(value) for key, value in values.items() if key in DEFAULTS and value is not None}
    for low, high in (("msg_rate_min_kop", "msg_rate_max_kop"), ("client_rate_min_kop", "client_rate_max_kop")):
        if low in clean and high in clean and clean[low] > clean[high]:
            raise FinanceError("Минимальная ставка не может быть больше максимальной", 422)
    for key, value in clean.items():
        if value < 0:
            raise FinanceError("Ставки не могут быть отрицательными", 422)
    if clean.get("slow_answer_sec", 1) <= clean.get("fast_answer_sec", 0):
        raise FinanceError("«Медленный» порог ответа должен быть больше «быстрого»", 422)
    if clean.get("order_percent_bp", 0) > 10000:
        raise FinanceError("Процент с заказов не может превышать 100 %", 422)
    return clean


# ---------------------------------------------------------------------- metrics
@dataclass
class Quality:
    messages: int = 0
    avg_chars: float = 0.0
    new_clients: int = 0
    median_answer_sec: float | None = None
    speed: float = 0.5
    fullness: float = 0.5
    coefficient: float = 0.5


async def _messages(session: AsyncSession, staff_id: int, lo: datetime, hi: datetime) -> tuple[int, float]:
    count, total_len = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(func.length(SupportThreadMessage.body)), 0),
            ).where(
                SupportThreadMessage.direction == "staff",
                SupportThreadMessage.kind == "message",
                SupportThreadMessage.staff_id == staff_id,
                SupportThreadMessage.created_at >= lo,
                SupportThreadMessage.created_at < hi,
            )
        )
    ).one()
    count = int(count or 0)
    return count, (float(total_len or 0) / count if count else 0.0)


async def _accepted_clients(
    session: AsyncSession, staff_id: int, lo: datetime, hi: datetime
) -> tuple[int, float | None]:
    """New clients this member answered first inside the period, and the median first answer."""
    initiated = (
        select(SupportThreadMessage.thread_id)
        .where(SupportThreadMessage.direction == "system", SupportThreadMessage.kind == "initiate")
        .scalar_subquery()
    )
    first_msg_id = (
        select(func.min(SupportThreadMessage.id))
        .where(SupportThreadMessage.direction == "staff", SupportThreadMessage.kind == "message")
        .group_by(SupportThreadMessage.thread_id)
        .scalar_subquery()
    )
    first = (
        await session.execute(
            select(SupportThreadMessage.thread_id, SupportThreadMessage.id, SupportThreadMessage.created_at).where(
                SupportThreadMessage.id.in_(first_msg_id),
                SupportThreadMessage.direction == "staff",
                SupportThreadMessage.staff_id == staff_id,
                SupportThreadMessage.created_at >= lo,
                SupportThreadMessage.created_at < hi,
                # A dialog the agent started is not a new client, even if the client answers.
                ~SupportThreadMessage.thread_id.in_(initiated),
            )
        )
    ).all()
    if not first:
        return 0, None

    thread_ids = [row.thread_id for row in first]
    client_rows = (
        await session.execute(
            select(SupportThreadMessage.thread_id, SupportThreadMessage.id, SupportThreadMessage.created_at).where(
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

    waits: list[float] = []
    for thread_id, message_id, created_at in first:
        known = last_client.get(thread_id)
        if known is None or known[0] > message_id or created_at is None:
            continue
        waits.append(max(0.0, (_awake(created_at) - _awake(known[1])).total_seconds()))
    return len(first), (median(waits) if waits else None)


def _score(waits: float | None, avg_chars: float, config: dict[str, int]) -> Quality:
    fast, slow = config["fast_answer_sec"], config["slow_answer_sec"]
    if waits is None:
        speed = 0.5  # nothing to measure: the middle of the range, never a reward or a punishment
    elif waits <= fast:
        speed = 1.0
    elif waits >= slow:
        speed = 0.0
    else:
        speed = 1.0 - (waits - fast) / (slow - fast)
    full = max(1, config["full_answer_chars"])
    fullness = min(1.0, max(0.0, avg_chars / full))
    return Quality(
        median_answer_sec=waits,
        speed=round(speed, 2),
        fullness=round(fullness, 2),
        coefficient=round(0.5 * speed + 0.5 * fullness, 2),
    )


async def _fines(session: AsyncSession, staff_id: int, lo: datetime, hi: datetime) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(FinanceOperation.amount_kop), 0)).where(
            FinanceOperation.staff_id == staff_id,
            FinanceOperation.kind == "fine",
            FinanceOperation.created_at >= lo,
            FinanceOperation.created_at < hi,
        )
    )
    return int(-(total or 0))


async def _reviews(session: AsyncSession, staff_id: int, lo: datetime, hi: datetime) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(SupportReview)
            .where(
                SupportReview.staff_id == staff_id,
                SupportReview.confirmed.is_(True),
                SupportReview.rating >= 5,
                SupportReview.created_at >= lo,
                SupportReview.created_at < hi,
            )
        )
        or 0
    )


async def _orders(session: AsyncSession, staff_id: int, lo: datetime, hi: datetime) -> tuple[int, int]:
    count, rubles = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(Order.paid_amount), 0)).where(
                Order.paid_staff_id == staff_id,
                Order.paid_at.is_not(None),
                Order.paid_at >= lo,
                Order.paid_at < hi,
                Order.paid_amount.is_not(None),
                Order.refunded_at.is_(None),
            )
        )
    ).one()
    return int(count or 0), int(rubles or 0) * 100


# ------------------------------------------------------------------------ report
async def compute(session: AsyncSession, staff: Staff, period: str, config: dict[str, int] | None = None) -> dict[str, Any]:
    config = config or await get_config(session)
    lo, hi = period_range(period)

    messages, avg_chars = await _messages(session, staff.telegram_id, lo, hi)
    new_clients, waits = await _accepted_clients(session, staff.telegram_id, lo, hi)
    quality = _score(waits, avg_chars, config)
    quality.messages = messages
    quality.avg_chars = round(avg_chars, 1)
    quality.new_clients = new_clients

    msg_rate = int(config["msg_rate_min_kop"] + (config["msg_rate_max_kop"] - config["msg_rate_min_kop"]) * quality.coefficient + 0.5)
    client_rate = int(
        config["client_rate_min_kop"]
        + (config["client_rate_max_kop"] - config["client_rate_min_kop"]) * quality.coefficient
        + 0.5
    )
    messages_kop = messages * msg_rate
    clients_kop = new_clients * client_rate

    orders_count, orders_base_kop = await _orders(session, staff.telegram_id, lo, hi)
    orders_kop = (orders_base_kop * config["order_percent_bp"] + 5000) // 10000
    reviews = await _reviews(session, staff.telegram_id, lo, hi)
    reviews_kop = reviews * config["review_bonus_kop"]
    fines = await _fines(session, staff.telegram_id, lo, hi)

    base_salary = int(staff.base_salary_kop or 0)
    salary_kop = base_salary + messages_kop + clients_kop
    premium_gross = orders_kop + reviews_kop
    # ТЗ 6.5: fines reduce the premium, they never turn it into a debt. A fine that
    # exceeds the premium stays in the balance (it is already posted as a fine op).
    premium_net = max(0, premium_gross - fines)
    fine_uncovered = max(0, fines - premium_gross)

    accrual = await session.scalar(
        select(PayrollAccrual).where(PayrollAccrual.staff_id == staff.telegram_id, PayrollAccrual.period == period)
    )
    return {
        "staff_id": staff.telegram_id,
        "name": staff.display_name or str(staff.telegram_id),
        "role": staff.role,
        "period": period,
        "base_salary_kop": base_salary,
        "messages_count": messages,
        "message_rate_kop": msg_rate,
        "messages_kop": messages_kop,
        "new_clients_count": new_clients,
        "client_rate_kop": client_rate,
        "clients_kop": clients_kop,
        "dialogue_kop": messages_kop + clients_kop,
        "salary_kop": salary_kop,
        "orders_count": orders_count,
        "orders_base_kop": orders_base_kop,
        "orders_kop": orders_kop,
        "reviews_count": reviews,
        "reviews_kop": reviews_kop,
        "premium_gross_kop": premium_gross,
        "fine_kop": fines,
        "fine_uncovered_kop": fine_uncovered,
        "premium_kop": premium_net,
        "total_kop": salary_kop + premium_net,
        "min_payout_kop": config["min_payout_kop"],
        "quality": {
            "speed": quality.speed,
            "fullness": quality.fullness,
            "coefficient": quality.coefficient,
            "avg_chars": quality.avg_chars,
            "median_answer_sec": quality.median_answer_sec,
        },
        "accrual": None if accrual is None else {
            "id": accrual.id, "salary_kop": accrual.salary_kop, "bonus_kop": accrual.bonus_kop,
            "fine_kop": accrual.fine_kop, "total_kop": accrual.total_kop, "reversed": accrual.reversed,
            "created_at": accrual.created_at.isoformat() if accrual.created_at else None,
            "salary_op_id": accrual.salary_op_id, "bonus_op_id": accrual.bonus_op_id,
        },
    }


async def report(session: AsyncSession, period: str) -> dict[str, Any]:
    config = await get_config(session)
    staff = list(
        await session.scalars(
            select(Staff).where(Staff.is_active.is_(True), Staff.role.in_(SUPPORT_ROLES)).order_by(Staff.display_name)
        )
    )
    rows = [await compute(session, member, period, config) for member in staff]
    # Members without money and without support activity are noise in a payroll table.
    rows = [row for row in rows if row["total_kop"] or row["messages_count"] or row["orders_count"]]
    keys = ("base_salary_kop", "dialogue_kop", "salary_kop", "orders_kop", "reviews_kop",
            "premium_gross_kop", "fine_kop", "fine_uncovered_kop", "premium_kop", "total_kop")
    return {
        "period": period,
        "config": config,
        "rows": rows,
        "members": [
            {"telegram_id": member.telegram_id, "display_name": member.display_name, "role": member.role}
            for member in staff
        ],
        "total": {key: sum(row[key] for row in rows) for key in keys},
        "counts": {
            "messages": sum(row["messages_count"] for row in rows),
            "new_clients": sum(row["new_clients_count"] for row in rows),
            "orders": sum(row["orders_count"] for row in rows),
            "reviews": sum(row["reviews_count"] for row in rows),
        },
    }


# ----------------------------------------------------------------------- posting
def _salary_comment(row: dict[str, Any]) -> str:
    parts = [f"оклад {rub(row['base_salary_kop'])} ₽"] if row["base_salary_kop"] else []
    if row["messages_count"]:
        parts.append(
            f"сообщения {row['messages_count']} × {rub(row['message_rate_kop'])} ₽ = {rub(row['messages_kop'])} ₽"
        )
    if row["new_clients_count"]:
        parts.append(
            f"новые клиенты {row['new_clients_count']} × {rub(row['client_rate_kop'])} ₽ = {rub(row['clients_kop'])} ₽"
        )
    parts.append(f"коэффициент {str(row['quality']['coefficient']).replace('.', ',')}")
    return "Зарплата за " + row["period"] + ": " + "; ".join(parts)


def _bonus_comment(row: dict[str, Any]) -> str:
    parts = []
    if row["orders_count"]:
        parts.append(f"заказы {row['orders_count']} на {rub(row['orders_base_kop'])} ₽ = {rub(row['orders_kop'])} ₽")
    if row["reviews_count"]:
        parts.append(f"отзывы {row['reviews_count']} × 100 ₽ = {rub(row['reviews_kop'])} ₽")
    return "Премия за " + row["period"] + ": " + "; ".join(parts)


async def accrue(session: AsyncSession, actor_id: int, period: str, staff_ids: list[int] | None = None) -> dict[str, Any]:
    """Post the month for the given members (all support members by default)."""
    period_range(period)  # validate before touching anything
    config = await get_config(session)
    stmt = select(Staff).where(Staff.is_active.is_(True), Staff.role.in_(SUPPORT_ROLES))
    if staff_ids:
        stmt = stmt.where(Staff.telegram_id.in_(staff_ids))
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for member in await session.scalars(stmt.order_by(Staff.display_name)):
        if member.telegram_id == actor_id:
            skipped.append({"staff_id": member.telegram_id, "name": member.display_name, "reason": "нельзя начислять самому себе"})
            continue
        existing = await session.scalar(
            select(PayrollAccrual).where(
                PayrollAccrual.staff_id == member.telegram_id, PayrollAccrual.period == period
            )
        )
        if existing is not None and not existing.reversed:
            skipped.append({"staff_id": member.telegram_id, "name": member.display_name, "reason": "уже начислено"})
            continue
        row = await compute(session, member, period, config)
        if row["salary_kop"] <= 0 and row["premium_gross_kop"] <= 0:
            skipped.append({"staff_id": member.telegram_id, "name": member.display_name, "reason": "нечего начислять"})
            continue

        salary_op = None
        if row["salary_kop"] > 0:
            salary_op = FinanceOperation(
                staff_id=member.telegram_id, kind="salary", amount_kop=row["salary_kop"],
                period=period, comment=_salary_comment(row), created_by=actor_id,
            )
            session.add(salary_op)
        bonus_op = None
        if row["premium_gross_kop"] > 0:
            bonus_op = FinanceOperation(
                staff_id=member.telegram_id, kind="bonus", amount_kop=row["premium_gross_kop"],
                period=period, comment=_bonus_comment(row), created_by=actor_id,
            )
            session.add(bonus_op)
        await session.flush()

        accrual = existing or PayrollAccrual(staff_id=member.telegram_id, period=period)
        accrual.salary_kop = row["salary_kop"]
        accrual.bonus_kop = row["premium_gross_kop"]
        accrual.fine_kop = row["fine_kop"]
        accrual.total_kop = row["total_kop"]
        accrual.details = row
        accrual.salary_op_id = salary_op.id if salary_op is not None else None
        accrual.bonus_op_id = bonus_op.id if bonus_op is not None else None
        accrual.created_by = actor_id
        accrual.reversed = False
        accrual.reversed_by = None
        accrual.reversed_at = None
        session.add(accrual)
        await session.flush()
        created.append({
            "staff_id": member.telegram_id, "name": member.display_name, "accrual_id": accrual.id,
            "salary_kop": row["salary_kop"], "bonus_kop": row["premium_gross_kop"], "total_kop": row["total_kop"],
        })
    return {"created": created, "skipped": skipped}


async def cancel(session: AsyncSession, actor_id: int, staff_id: int, period: str) -> dict[str, Any]:
    accrual = await session.scalar(
        select(PayrollAccrual).where(PayrollAccrual.staff_id == staff_id, PayrollAccrual.period == period)
    )
    if accrual is None:
        raise FinanceError("Начисление за этот месяц не найдено", 404)
    if accrual.reversed:
        raise FinanceError("Начисление уже отменено", 409)
    for op_id in (accrual.salary_op_id, accrual.bonus_op_id):
        if op_id:
            await ledger.reverse(session, op_id, actor_id, f"Отмена начисления за {period}")
    accrual.reversed = True
    accrual.reversed_by = actor_id
    accrual.reversed_at = datetime.now(timezone.utc)
    await session.flush()
    return {"ok": True}


# ----------------------------------------------------------------------- reviews
async def list_reviews(session: AsyncSession, period: str | None, staff_id: int | None) -> list[dict[str, Any]]:
    stmt = select(SupportReview).order_by(SupportReview.id.desc()).limit(200)
    if staff_id:
        stmt = stmt.where(SupportReview.staff_id == staff_id)
    if period:
        lo, hi = period_range(period)
        stmt = stmt.where(SupportReview.created_at >= lo, SupportReview.created_at < hi)
    rows = list(await session.scalars(stmt))
    names = dict(
        (await session.execute(
            select(Staff.telegram_id, Staff.display_name).where(Staff.telegram_id.in_({r.staff_id for r in rows}))
        )).all()
    ) if rows else {}
    return [
        {
            "id": r.id, "staff_id": r.staff_id, "staff_name": names.get(r.staff_id, str(r.staff_id)),
            "source": r.source, "source_label": REVIEW_SOURCES.get(r.source, r.source),
            "rating": r.rating, "order_id": r.order_id, "comment": r.comment, "confirmed": r.confirmed,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


async def add_review(
    session: AsyncSession, actor_id: int, *, staff_id: int, source: str, rating: int,
    order_id: int | None, comment: str | None,
) -> SupportReview:
    if source not in REVIEW_SOURCES:
        raise FinanceError("Неизвестный источник отзыва", 422)
    if not 1 <= rating <= 5:
        raise FinanceError("Оценка — от 1 до 5", 422)
    if await session.get(Staff, staff_id) is None:
        raise FinanceError("Сотрудник не найден", 404)
    review = SupportReview(
        staff_id=staff_id, source=source, rating=rating, order_id=order_id,
        comment=(comment or "").strip() or None, confirmed=True, created_by=actor_id,
    )
    session.add(review)
    await session.flush()
    return review


async def delete_review(session: AsyncSession, review_id: int) -> None:
    review = await session.get(SupportReview, review_id)
    if review is None:
        raise FinanceError("Отзыв не найден", 404)
    await session.delete(review)
    await session.flush()


async def min_payout(session: AsyncSession) -> int:
    return (await get_config(session))["min_payout_kop"]