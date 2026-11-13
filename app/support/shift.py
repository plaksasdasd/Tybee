"""Support shifts: start/end, presence heartbeat and the automatic end.

A shift is not just a checkbox: while it is on, the panel tab pings the server
(``last_seen_at``) and reports real input (``last_active_at``). The sweep ends the
shift when the employee is gone, so nobody "sits on shift" with the tab closed or
the computer left alone, and their waiting clients go back to the queue:

* ``offline``  — no open panel tab for OFFLINE_AFTER;
* ``idle``     — tab open but no mouse/keyboard for IDLE_AFTER;
* ``no_reply`` — a client has been waiting NO_REPLY_AFTER in a thread the
  employee has not even opened (catches mouse jigglers too).

Every shift is a ``staff_shifts`` row, so leads see real hours, not a flag.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Staff, StaffShift, SupportThread
from app.support.common import now

_settings = get_settings()
OFFLINE_AFTER = timedelta(minutes=_settings.support_shift_offline_minutes)
IDLE_AFTER = timedelta(minutes=_settings.support_shift_idle_minutes)
NO_REPLY_AFTER = timedelta(minutes=_settings.support_shift_no_reply_minutes)

MANUAL = "manual"
LOGOUT = "logout"
OFFLINE = "offline"
IDLE = "idle"
NO_REPLY = "no_reply"
DEACTIVATED = "deactivated"
AUTO_REASONS = (OFFLINE, IDLE, NO_REPLY)

REASON_LABELS = {
    MANUAL: "завершена вручную",
    LOGOUT: "выход из панели",
    OFFLINE: "панель закрыта",
    IDLE: f"нет активности {int(IDLE_AFTER.total_seconds() // 60)} мин",
    NO_REPLY: f"клиент ждал ответа {int(NO_REPLY_AFTER.total_seconds() // 60)} мин",
    DEACTIVATED: "сотрудник отключён",
}


async def open_shift(session: AsyncSession, staff_id: int) -> StaffShift | None:
    return await session.scalar(
        select(StaffShift)
        .where(StaffShift.staff_id == staff_id, StaffShift.ended_at.is_(None))
        .order_by(StaffShift.started_at.desc())
        .limit(1)
    )


async def last_shift(session: AsyncSession, staff_id: int) -> StaffShift | None:
    return await session.scalar(
        select(StaffShift).where(StaffShift.staff_id == staff_id).order_by(StaffShift.id.desc()).limit(1)
    )


def touch(staff: Staff, active: bool, at: datetime | None = None) -> None:
    at = at or now()
    staff.last_seen_at = at
    if active:
        staff.last_active_at = at


async def start_shift(session: AsyncSession, staff: Staff) -> bool:
    """False when the shift is already running."""
    staff = await session.scalar(select(Staff).where(Staff.telegram_id == staff.telegram_id).with_for_update().execution_options(populate_existing=True))
    if staff is None:
        return False
    if await open_shift(session, staff.telegram_id) is not None:
        return False
    at = now()
    staff.on_shift = True
    touch(staff, True, at)
    session.add(StaffShift(staff_id=staff.telegram_id, started_at=at))
    await session.flush()
    return True


async def end_shift(session: AsyncSession, staff: Staff, reason: str) -> bool:
    """False when the employee was not on shift."""
    staff = await session.scalar(select(Staff).where(Staff.telegram_id == staff.telegram_id).with_for_update().execution_options(populate_existing=True))
    if staff is None:
        return False
    rows = list(await session.scalars(select(StaffShift).where(StaffShift.staff_id == staff.telegram_id, StaffShift.ended_at.is_(None))))
    if not staff.on_shift and not rows:
        return False
    staff.on_shift = False
    for row in rows:
        row.ended_at = now()
        row.end_reason = reason
    if reason not in AUTO_REASONS:
        await release_waiting(session, staff.telegram_id)
    await session.flush()
    return True


async def release_waiting(session: AsyncSession, staff_id: int) -> int:
    """Return unanswered threads and unfinished next steps to the queue (the rest stay with the employee)."""
    result = await session.execute(
        update(SupportThread)
        .where(SupportThread.assigned_to == staff_id, SupportThread.status == "open", or_(SupportThread.needs_reply.is_(True), SupportThread.next_step.is_not(None)))
        .values(assigned_to=None, assigned_at=None)
    )
    return result.rowcount or 0


@dataclass
class AutoEnd:
    staff: Staff
    reason: str
    released: int


async def sweep(session: AsyncSession, at: datetime | None = None) -> list[AutoEnd]:
    """End shifts of employees who are not really there. The caller commits."""
    at = at or now()
    on_shift = Staff.on_shift.is_(True)
    found: dict[int, tuple[Staff, str]] = {}

    offline = await session.scalars(
        select(Staff).where(on_shift, or_(Staff.last_seen_at.is_(None), Staff.last_seen_at < at - OFFLINE_AFTER))
    )
    for s in offline:
        found.setdefault(s.telegram_id, (s, OFFLINE))

    idle = await session.scalars(
        select(Staff).where(on_shift, or_(Staff.last_active_at.is_(None), Staff.last_active_at < at - IDLE_AFTER))
    )
    for s in idle:
        found.setdefault(s.telegram_id, (s, IDLE))

    cutoff = at - NO_REPLY_AFTER
    # A client waits in a thread the employee has not opened since: the message, the
    # assignment and the shift itself are all older than the cutoff.
    waiting = await session.scalars(
        select(Staff)
        .join(SupportThread, SupportThread.assigned_to == Staff.telegram_id)
        .join(StaffShift, (StaffShift.staff_id == Staff.telegram_id) & StaffShift.ended_at.is_(None))
        .where(
            on_shift,
            SupportThread.status == "open",
            SupportThread.needs_reply.is_(True),
            SupportThread.next_step.is_(None),
            SupportThread.last_client_message_at < cutoff,
            or_(SupportThread.assigned_at.is_(None), SupportThread.assigned_at < cutoff),
            StaffShift.started_at < cutoff,
        )
        .distinct()
    )
    for s in waiting:
        found.setdefault(s.telegram_id, (s, NO_REPLY))

    ended: list[AutoEnd] = []
    for staff, reason in found.values():
        await end_shift(session, staff, reason)
        ended.append(AutoEnd(staff, reason, await release_waiting(session, staff.telegram_id)))
    return ended
