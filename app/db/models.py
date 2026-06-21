from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.states import UserState


class OrderType(str, Enum):
    ARTICLE = "article"
    PERSONAL = "personal"


class OrderStatus(str, Enum):
    NEW = "new"
    AWAITING_PAYMENT = "awaiting_payment"
    PAYMENT_REVIEW = "payment_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    IN_DIALOG = "in_dialog"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(256), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(32), default=UserState.MENU.value)
    # The single "screen" message we keep editing instead of deleting/resending.
    screen_message_id: Mapped[int | None] = mapped_column(BigInteger)
    current_order_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    orders: Mapped[list["Order"]] = relationship(back_populates="user")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(256))
    price: Mapped[int] = mapped_column(Integer)
    available: Mapped[bool] = mapped_column(Boolean, default=True)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default=OrderStatus.NEW.value)
    article: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(256))
    price: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="orders")


class MediaCache(Base):
    """Caches Telegram file_id per banner so we don't re-upload every edit."""

    __tablename__ = "media_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_id: Mapped[str] = mapped_column(String(256))


class RelayMap(Base):
    """Maps a message the bot posted in the support chat back to the user, so an
    admin reply (a Telegram reply) can be routed to the right customer."""

    __tablename__ = "relay_map"

    support_message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
