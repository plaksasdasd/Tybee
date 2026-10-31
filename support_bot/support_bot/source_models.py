from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from support_bot.source_db import SourceBase


class SourceUser(SourceBase):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(256))
    is_banned: Mapped[bool] = mapped_column(Boolean)
    current_order_id: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(16))


class SourceOrder(SourceBase):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24))
    article: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(256))
    price: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)


class SourceConversation(SourceBase):
    __tablename__ = "support_conversations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    channel: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))


class SourceInbox(SourceBase):
    __tablename__ = "support_inbox"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger)
    order_id: Mapped[int | None] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(24))
    body: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(160))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceOutbox(SourceBase):
    __tablename__ = "support_outbox"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger)
    order_id: Mapped[int | None] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(24), default="text")
    body: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
