"""Team leaderboard: who did what today, this week and this month.

Open to every employee on purpose — it is a shared scoreboard, not a management tool.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Staff, StaffRole
from app.support import leaderboard
from app.timeutil import PERIODS
from panel.auth import csrf_guard, current_staff
from panel.deps import get_session
from panel.permissions import ROLE_LABELS, Perm, perms_of

router = APIRouter(prefix="/api", tags=["leaderboard"], dependencies=[Depends(csrf_guard)])


def _role_label(role: str) -> str:
    try:
        return ROLE_LABELS[StaffRole(role)]
    except (ValueError, KeyError):
        return role


@router.get("/support/leaderboard")
async def support_leaderboard(
    period: str = Query("day", pattern="^(day|week|month)$"),
    me: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    if period not in PERIODS:  # the query pattern already says so; keep the service honest too
        raise HTTPException(422, "Период: day, week или month")
    staff = [
        member
        for member in await session.scalars(
            select(Staff).where(Staff.is_active.is_(True)).order_by(Staff.display_name)
        )
        if Perm.SUPPORT_CHAT in perms_of(member)
    ]
    data = await leaderboard.build(session, staff, period)
    for row in data["rows"]:
        row["role_label"] = _role_label(row["role"])
        row["is_me"] = row["staff_id"] == me.telegram_id
    return data