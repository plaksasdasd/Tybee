"""Thread assignment: claim, transfer and release on deactivation."""
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Staff, SupportThread, SupportThreadMessage
from app.support.common import SupportError, now


async def claim(session: AsyncSession, thread_id: int, staff: Staff) -> bool:
    """Atomically take a queued thread. False when somebody got there first."""
    result = await session.execute(
        update(SupportThread)
        .where(SupportThread.id == thread_id, SupportThread.assigned_to.is_(None))
        .values(
            assigned_to=staff.telegram_id,
            assigned_at=now(),
            transfer_requested_by=None,
            transfer_requested_at=None,
        )
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
    thread.transfer_requested_by = None
    thread.transfer_requested_at = None
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
        .values(assigned_to=None, assigned_at=None, transfer_requested_by=None, transfer_requested_at=None)
    )
    return result.rowcount or 0
