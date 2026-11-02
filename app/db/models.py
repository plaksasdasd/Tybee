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
    MYSTERY_BOX = "mystery_box"


class OrderStatus(str, Enum):
    NEW = "new"
    AWAITING_PAYMENT = "awaiting_payment"
    PAYMENT_REVIEW = "payment_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    IN_DIALOG = "in_dialog"
    ACCEPTED = "accepted"
    BOUGHT = "bought"
    SHIPPING_TO_RU = "shipping_to_ru"
    CUSTOMS = "customs"
    SHIPPING_IN_RU = "shipping_in_ru"
    AWAITING_PICKUP = "awaiting_pickup"
    RECEIVED = "received"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(256), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(32), default=UserState.MENU.value)
    # The single "screen" message we keep editing instead of deleting/resending.
    screen_message_id: Mapped[int | None] = mapped_column(BigInteger)
    current_order_id: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(16), default="telegram")
    avito_user_id: Mapped[int | None] = mapped_column(BigInteger)
    avito_chat_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    orders: Mapped[list["Order"]] = relationship(back_populates="user")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(256))
    price: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    # Telegram file_id of the product photo supplied by the admin.
    photo_file_id: Mapped[str | None] = mapped_column(String(256))
    available: Mapped[bool] = mapped_column(Boolean, default=True)


class MysteryBox(Base):
    """A Mystery Box variant offered to clients, managed via the /admin panel."""

    __tablename__ = "mystery_boxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256))
    price: Mapped[int] = mapped_column(Integer)
    available: Mapped[bool] = mapped_column(Boolean, default=True)


class Order(Base):
    __tablename__ = "orders"

    # Public order identifier: deliberately unrelated to the database sequence.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
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


class SupportConversation(Base):
    __tablename__ = "support_conversations"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    channel: Mapped[str] = mapped_column(String(16), default="telegram")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SupportInbox(Base):
    __tablename__ = "support_inbox"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("support_conversations.id"), index=True
    )
    order_id: Mapped[int | None] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(24), default="message")
    body: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(160), unique=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SupportOutbox(Base):
    __tablename__ = "support_outbox"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(24), default="text")
    body: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentCharge(Base):
    __tablename__ = "payment_charges"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    telegram_payment_charge_id: Mapped[str] = mapped_column(String(128), unique=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())