from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from support_bot.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_settings = get_settings()
engine = create_async_engine(_settings.database_url, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

_COLUMN_PATCHES = (
    "ALTER TABLE support_requests ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'direct'",
    "ALTER TABLE support_requests ADD COLUMN IF NOT EXISTS channel VARCHAR(16) NOT NULL DEFAULT 'telegram'",
    "ALTER TABLE support_requests ADD COLUMN IF NOT EXISTS source_conversation_id BIGINT",
    "ALTER TABLE support_requests ADD COLUMN IF NOT EXISTS source_order_id BIGINT",
    "ALTER TABLE support_messages ADD COLUMN IF NOT EXISTS source_event_id BIGINT",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_support_requests_source_conversation ON support_requests (source_conversation_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_support_messages_source_event ON support_messages (source_event_id)",
)


async def init_models() -> None:
    from support_bot import models  # noqa: F401

    for attempt in range(10):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                for statement in _COLUMN_PATCHES:
                    await conn.execute(text(statement))
            return
        except Exception:
            logger.warning("DB not ready (attempt %d/10), retrying in 2s...", attempt + 1)
            await asyncio.sleep(2)
    raise RuntimeError("Could not connect to database after 10 attempts")
