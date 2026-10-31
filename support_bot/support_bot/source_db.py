from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from support_bot.config import get_settings


class SourceBase(DeclarativeBase):
    pass


_settings = get_settings()
_source_url = None
if _settings.source_postgres_host.strip():
    _source_url = (
        f"postgresql+asyncpg://{_settings.source_postgres_user}:{_settings.source_postgres_password}"
        f"@{_settings.source_postgres_host}:{_settings.source_postgres_port}/{_settings.source_postgres_db}"
    )

source_engine = create_async_engine(_source_url, pool_pre_ping=True) if _source_url else None
source_session_factory = (
    async_sessionmaker(source_engine, class_=AsyncSession, expire_on_commit=False)
    if source_engine is not None
    else None
)


def source_enabled() -> bool:
    return source_session_factory is not None
