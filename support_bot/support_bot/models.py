from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from support_bot.db import Base
from support_bot.states import RequestStatus, UserState


class BotUser(Base):
    __tablename__ = "bot_users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(256), default="")
    state: Mapped[str] = mapped_column(String(32), default=UserState.MENU.value)
    active_request_id: Mapped[int | None] = mapped_column(BigInteger)


class Staff(Base):
    __tablename__ = "staff"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    display_name: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SupportRequest(Base):
    __tablename__ = "support_requests"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    customer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    customer_name: Mapped[str] = mapped_column(String(256), default="")
    customer_username: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), default="direct")
    channel: Mapped[str] = mapped_column(String(16), default="telegram")
    source_conversation_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    source_order_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(
        String(16), default=RequestStatus.NEW.value, index=True
    )
    assigned_to: Mapped[int | None] = mapped_column(BigInteger, index=True)
    assigned_name: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("support_requests.id"), index=True
    )
    sender_id: Mapped[int] = mapped_column(BigInteger)
    sender_kind: Mapped[str] = mapped_column(String(16))
    body: Mapped[str] = mapped_column(Text)
    source_event_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RequestNotification(Base):
    __tablename__ = "request_notifications"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("support_requests.id"), index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
