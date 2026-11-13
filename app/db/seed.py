from __future__ import annotations

from sqlalchemy import update

from app.db.base import session_factory
from app.db.models import UserCover
from app.states import UserState

ADMIN_STATES = tuple(s.value for s in UserState if s.name.startswith("ADMIN_"))


async def reset_admin_states(factory=None) -> int:
    """The bots' /admin panel is gone (ТЗ-2, D.5): clients stuck in an ADMIN_* state go back to the menu."""
    async with (factory or session_factory)() as session:
        result = await session.execute(
            update(UserCover).where(UserCover.state.in_(ADMIN_STATES)).values(state=UserState.MENU.value)
        )
        await session.commit()
        return result.rowcount or 0
