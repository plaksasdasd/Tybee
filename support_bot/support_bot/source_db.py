from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from support_bot.config import get_settings


class SourceBase(DeclarativeBase):
    pass


source_engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
source_session_factory = async_sessionmaker(
    source_engine, class_=AsyncSession, expire_on_commit=False
)


def source_enabled() -> bool:
    return True
