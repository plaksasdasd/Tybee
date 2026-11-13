"""Thread assignment: automatic (least loaded client manager on shift), claim and transfer."""
from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Staff, SupportThread, SupportThreadMessage
from app.support.common import ASSIGNABLE_ROLES, SupportError, now, ts


async def auto_assign(session: AsyncSession, thread: SupportThread) -> Staff | None:
    candidates = list(
        await session.scalars(
            select(Staff).where(
                Staff.role.in_(ASSIGNABLE_ROLES), Staff.is_active.is_(True), Staff.on_shift.is_(True)
            )
        )
    )
    if not candidates:
        return None
    load = dict(
        (await session.execute(
            select(SupportThread.assigned_to, func.count())
            .where(SupportThread.status == "open", SupportThread.assigned_to.in_([c.telegram_id for c in candidates]))
            .group_by(SupportThread.assigned_to)
        )).all()
    )
    best = min(candidates, key=lambda c: (load.get(c.telegram_id, 0), ts(c.last_assigned_at), c.telegram_id))
    thread.assigned_to = best.telegram_id
    thread.assigned_at = now()
    best.last_assigned_at = now()
    return best


async def claim(session: AsyncSession, thread_id: int, staff: Staff) -> bool:
    """Atomically take a queued thread. False when somebody got there first."""
    result = await session.execute(
        update(SupportThread)
        .where(SupportThread.id == thread_id, SupportThread.assigned_to.is_(None))
        .values(assigned_to=staff.telegram_id, assigned_at=now())
    )
    if result.rowcount != 1:
        return False
    staff.last_assigned_at = now()
    session.add(
        SupportThreadMessage(
            thread_id=thread_id, direction="system", kind="assign", staff_id=staff.telegram_id,
            body=f"Ветку взял в работу: {staff.display_name}",
        )
    )
    return True


async def transfer(session: AsyncSession, thread: SupportThread, target: Staff, by: Staff) -> None:
    if not target.is_active:
        raise SupportError("Сотрудник отключён", 422)
    thread.assigned_to = target.telegram_id
    thread.assigned_at = now()
    target.last_assigned_at = now()
    session.add(
        SupportThreadMessage(
            thread_id=thread.id, direction="system", kind="assign", staff_id=by.telegram_id,
            body=f"{by.display_name} передал(а) ветку сотруднику {target.display_name}",
        )
    )


async def release_threads(session: AsyncSession, staff_id: int) -> int:
    """Return a deactivated employee's threads to the queue."""
    result = await session.execute(
        update(SupportThread)
        .where(SupportThread.assigned_to == staff_id)
        .values(assigned_to=None, assigned_at=None)
    )
    return result.rowcount or 0
