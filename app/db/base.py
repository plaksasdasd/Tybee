from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_settings = get_settings()
engine = create_async_engine(_settings.database_url, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Lightweight, idempotent column additions for databases created before these
# columns existed (the project ships no migration tool; create_all only adds
# whole tables, never new columns).
_COLUMN_PATCHES = (
    "ALTER TABLE orders ALTER COLUMN id TYPE BIGINT",
    "ALTER TABLE orders ALTER COLUMN id DROP DEFAULT",
    "ALTER TABLE users ALTER COLUMN current_order_id TYPE BIGINT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS support_thread_id BIGINT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'telegram'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS avito_user_id BIGINT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS avito_chat_id VARCHAR(64)",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS description TEXT",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS photo_file_id VARCHAR(256)",
    "CREATE INDEX IF NOT EXISTS ix_users_username_lower ON users (lower(username))",
    "CREATE INDEX IF NOT EXISTS ix_users_support_thread_id ON users (support_thread_id)",
    "CREATE INDEX IF NOT EXISTS ix_users_avito_chat_id ON users (avito_chat_id)",
    # An older bug stored empty file_ids — they make Telegram reject sends
    # with MEDIA_EMPTY; wipe them so banners re-upload from disk.
    "DELETE FROM media_cache WHERE file_id = ''",
)

# Avito client ids are shifted into a range Telegram ids never reach, so the
# two id spaces share the users table without colliding.
AVITO_ID_BASE = 10 ** 12


async def init_models() -> None:
    # Import models so they are registered on Base.metadata before create_all.
    from app.db import models  # noqa: F401

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
