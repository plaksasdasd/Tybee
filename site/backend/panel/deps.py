from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import base as db_base


async def get_session() -> AsyncIterator[AsyncSession]:
    # Looked up on every call so tests can swap ``db_base.session_factory``.
    async with db_base.session_factory() as session:
        yield session
