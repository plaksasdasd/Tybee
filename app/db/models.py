from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    true,
    text,
)
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
    REFUND_PENDING = "refund_pending"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


_PK = BigInteger().with_variant(Integer, "sqlite")


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
    # The cover (bot) the order was created in; every client-facing message
    # about the order goes out through that cover's bot.
    cover_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("covers.id"), index=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Set by the Robokassa webhook; rubles, like ``price``. ``paid_staff_id`` is
    # whoever owned the client's support thread at the moment of payment.
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    paid_amount: Mapped[int | None] = mapped_column(Integer)
    paid_staff_id: Mapped[int | None] = mapped_column(BigInteger)
    # ТЗ-2 block B: refunds (rubles) and the last /ping reminder.
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    refund_amount: Mapped[int | None] = mapped_column(Integer)
    last_ping_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # ТЗ-2 block C: Avito profile the invoice went through (Avito clients only).
    avito_profile_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("avito_profiles.id"), nullable=True)

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
    # Cover the client wrote from (informational for support).
    cover_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Avito chat (and through it the Avito profile) the message came from.
    avito_chat_ref: Mapped[int | None] = mapped_column(_PK, ForeignKey("avito_chats.id"), nullable=True)
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
    # Optional explicit delivery cover; when empty the cover is resolved from
    # the order (order.cover_id) or the client's activity (resolve_reply_cover).
    cover_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Explicit Avito chat to answer in (see app/services/outbox.py, ТЗ-2 C.5).
    avito_chat_ref: Mapped[int | None] = mapped_column(_PK, ForeignKey("avito_chats.id"), nullable=True)
    # File sent together with the message (support_attachments.id), if any.
    attachment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attachment_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    body_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    delivery_uncertain: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RobokassaPayment(Base):
    __tablename__ = "robokassa_payments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # Cover the unban payment was started from (confirmation goes back there).
    cover_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Cover(Base):
    """A "cover": one Telegram bot with its own folder of static content
    (token, texts, buttons, banners). All covers share the same code and DB."""

    __tablename__ = "covers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True)
    # Absolute, or relative to settings.covers_root.
    path: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    last_error: Mapped[str | None] = mapped_column(Text)
    bot_id: Mapped[int | None] = mapped_column(BigInteger)
    bot_username: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserCover(Base):
    """Per (client x cover) conversational state."""

    __tablename__ = "user_covers"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    cover_id: Mapped[int] = mapped_column(Integer, ForeignKey("covers.id"), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), default=UserState.MENU.value)
    screen_message_id: Mapped[int | None] = mapped_column(BigInteger)
    current_order_id: Mapped[int | None] = mapped_column(BigInteger)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=func.now(), onupdate=func.now()
    )


class CoverMediaCache(Base):
    """Telegram file_id of a banner per cover (file_ids are bot-specific).

    ``fingerprint`` is "{size}:{mtime_ns}" of the banner file; a mismatch means
    the file changed on disk and must be re-uploaded."""

    __tablename__ = "cover_media_cache"

    cover_id: Mapped[int] = mapped_column(Integer, ForeignKey("covers.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_id: Mapped[str] = mapped_column(String(256))
    fingerprint: Mapped[str] = mapped_column(String(64), default="")


class CoverProductPhoto(Base):
    """Telegram file_id of a product photo per cover."""

    __tablename__ = "cover_product_photos"

    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)
    cover_id: Mapped[int] = mapped_column(Integer, ForeignKey("covers.id"), primary_key=True)
    file_id: Mapped[str] = mapped_column(String(256))


class StaffRole(str, Enum):
    ARBITRATION_AGENT = "arbitration_agent"
    BUYOUT_AGENT = "buyout_agent"
    CLIENT_MANAGER_1 = "client_manager_1"
    CLIENT_MANAGER_2 = "client_manager_2"
    ADMIN = "admin"
    SUPPORT_LEAD = "support_lead"
    FINANCE_MANAGER = "finance_manager"


class Staff(Base):
    """An employee allowed to sign in to the web panel."""

    __tablename__ = "staff"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(32), default=StaffRole.ADMIN.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    username: Mapped[str | None] = mapped_column(String(64))
    photo_url: Mapped[str | None] = mapped_column(Text)
    position: Mapped[str | None] = mapped_column(String(128))
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    # Support shift: only staff "on shift" receive auto-assigned threads.
    on_shift: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    last_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Presence while on shift: any open panel tab pings ``last_seen_at``; real input
    # (mouse/keyboard) also moves ``last_active_at``. See app/support/shift.py.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Where to send money; free text, shown only to the owner and payout approvers.
    payout_requisites: Mapped[str | None] = mapped_column(Text)
    # Fixed monthly salary (оклад) in kopecks; set by an admin, paid by the payroll run.
    base_salary_kop: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=func.now(), onupdate=func.now()
    )


class IdempotentRequest(Base):
    """Transactional claim and result for a single actor/action/request key."""
    __tablename__ = "idempotent_requests"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    result_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PanelSession(Base):
    """Revocable panel session; only the hash of the bearer token is stored."""
    __tablename__ = "panel_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    staff_id: Mapped[int] = mapped_column(BigInteger, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class StaffShift(Base):
    """One support shift: when it started, when and why it ended (manual, idle, offline, ...)."""

    __tablename__ = "staff_shifts"
    __table_args__ = (
        Index("ix_staff_shifts_staff_started", "staff_id", "started_at"),
        Index("uq_staff_open_shift", "staff_id", unique=True,
              postgresql_where=text("ended_at IS NULL"), sqlite_where=text("ended_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    staff_id: Mapped[int] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(16))


class StaffInvite(Base):
    """One-time link: whoever opens it and signs in via Telegram becomes staff with the preset role."""

    __tablename__ = "staff_invites"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(32))
    display_name: Mapped[str | None] = mapped_column(String(128))
    position: Mapped[str | None] = mapped_column(String(128))
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[int | None] = mapped_column(BigInteger)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class AuditLog(Base):
    """Who changed what in the web panel. ``details`` never holds tokens."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_actor_id", "actor_id"),
        Index("ix_audit_log_action", "action"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=func.now())


class SupportThread(Base):
    """One permanent support thread per client (all covers and Avito)."""

    __tablename__ = "support_threads"
    __table_args__ = (
        Index("ix_support_threads_assigned_status", "assigned_to", "status"),
        Index("ix_support_threads_last_message_at", "last_message_at"),
    )

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), unique=True)
    assigned_to: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="open")
    last_cover_id: Mapped[int | None] = mapped_column(Integer)
    last_avito_chat_ref: Mapped[int | None] = mapped_column(_PK, ForeignKey("avito_chats.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), default="telegram")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_client_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unread: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    needs_reply: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    waiting_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_through_id: Mapped[int | None] = mapped_column(BigInteger)
    handoff_note: Mapped[str | None] = mapped_column(Text)
    next_step: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=func.now())


class SupportThreadMessage(Base):
    __tablename__ = "support_thread_messages"

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(_PK, ForeignKey("support_threads.id"), index=True)
    direction: Mapped[str] = mapped_column(String(16))  # client / staff / system
    staff_id: Mapped[int | None] = mapped_column(BigInteger)
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(24), default="message")
    cover_id: Mapped[int | None] = mapped_column(Integer)
    order_id: Mapped[int | None] = mapped_column(BigInteger)
    request_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    inbox_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    outbox_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=func.now())


class FinanceOperation(Base):
    """Append-only money ledger (kopecks). Mistakes are fixed by a reversal row, never UPDATE/DELETE."""

    __tablename__ = "finance_operations"
    __table_args__ = (
        Index("ix_finance_operations_staff_created", "staff_id", "created_at"),
        Index("ix_finance_operations_kind_created", "kind", "created_at"),
    )

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    staff_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("staff.telegram_id"))
    kind: Mapped[str] = mapped_column(String(16))  # salary bonus fine adjustment payout reversal
    amount_kop: Mapped[int] = mapped_column(BigInteger)  # signed
    period: Mapped[str | None] = mapped_column(String(7))  # YYYY-MM
    comment: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    payout_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    reversal_of: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=func.now())


class PayoutRequest(Base):
    __tablename__ = "payout_requests"
    __table_args__ = (Index("ix_payout_requests_status", "status"),)

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    staff_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("staff.telegram_id"), index=True)
    amount_kop: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="requested")  # requested approved rejected paid cancelled
    requisites: Mapped[str] = mapped_column(Text)  # snapshot at request time
    comment: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[int | None] = mapped_column(BigInteger)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str | None] = mapped_column(Text)
    paid_by: Mapped[int | None] = mapped_column(BigInteger)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_reference: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=func.now())


class SupportReview(Base):
    """A client review that earns the support bonus (ТЗ 6.4). Only the top mark pays out.

    Reviews come from the marketplaces and messengers the client used; ``confirmed``
    is set once the review was verified (screenshot, profile link, Avito rating)."""

    __tablename__ = "support_reviews"
    __table_args__ = (Index("ix_support_reviews_staff_created", "staff_id", "created_at"),)

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    staff_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("staff.telegram_id"), index=True)
    source: Mapped[str] = mapped_column(String(16), default="avito")  # avito telegram yandex
    rating: Mapped[int] = mapped_column(Integer, default=5)
    order_id: Mapped[int | None] = mapped_column(BigInteger)
    comment: Mapped[str | None] = mapped_column(Text)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=func.now())


class PayrollSetting(Base):
    """Rates of the support payroll (ТЗ 6.2-6.4, 6.6). One row, id = 1.

    Rates are kopecks; ``order_percent_bp`` is a percent in basis points (250 = 2,5 %)."""

    __tablename__ = "payroll_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    # Per outgoing message: dynamic rate between min and max (quality coefficient).
    msg_rate_min_kop: Mapped[int] = mapped_column(Integer, default=50, server_default="50")
    msg_rate_max_kop: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    # Per accepted new client: dynamic rate between min and max.
    client_rate_min_kop: Mapped[int] = mapped_column(Integer, default=300, server_default="300")
    client_rate_max_kop: Mapped[int] = mapped_column(Integer, default=500, server_default="500")
    order_percent_bp: Mapped[int] = mapped_column(Integer, default=250, server_default="250")
    review_bonus_kop: Mapped[int] = mapped_column(Integer, default=10000, server_default="10000")
    min_payout_kop: Mapped[int] = mapped_column(Integer, default=150000, server_default="150000")
    # Quality coefficient inputs: fast/slow answer boundaries and the "full answer" length.
    fast_answer_sec: Mapped[int] = mapped_column(Integer, default=120, server_default="120")
    slow_answer_sec: Mapped[int] = mapped_column(Integer, default=1800, server_default="1800")
    full_answer_chars: Mapped[int] = mapped_column(Integer, default=200, server_default="200")
    updated_by: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=func.now(), onupdate=func.now())


class PayrollAccrual(Base):
    """One posted payroll run for a member and a month (ТЗ 6.6: the period is a calendar month).

    ``details`` keeps the calculation snapshot, so the report stays readable after
    rates change. Money is only ever posted to ``finance_operations``; this row is
    the idempotency key and the audit trail."""

    __tablename__ = "payroll_accruals"
    __table_args__ = (UniqueConstraint("staff_id", "period", name="uq_payroll_accruals_staff_period"),)

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    staff_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("staff.telegram_id"), index=True)
    period: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    salary_kop: Mapped[int] = mapped_column(BigInteger, default=0)
    bonus_kop: Mapped[int] = mapped_column(BigInteger, default=0)
    fine_kop: Mapped[int] = mapped_column(BigInteger, default=0)
    total_kop: Mapped[int] = mapped_column(BigInteger, default=0)
    details: Mapped[dict | None] = mapped_column(JSON)
    salary_op_id: Mapped[int | None] = mapped_column(BigInteger)
    bonus_op_id: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=func.now())
    reversed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    reversed_by: Mapped[int | None] = mapped_column(BigInteger)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LogisticsRequest(Base):
    __tablename__ = "logistics_requests"

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("staff.telegram_id"), index=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("orders.id"), nullable=True)
    purpose: Mapped[str] = mapped_column(String(256))
    amount_kop: Mapped[int] = mapped_column(BigInteger)
    requisites: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="requested", index=True)
    decided_by: Mapped[int | None] = mapped_column(BigInteger)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str | None] = mapped_column(Text)
    paid_by: Mapped[int | None] = mapped_column(BigInteger)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_reference: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=func.now())


class SupportAttachment(Base):
    """A file in the support chat. Stored at ``{DATA_DIR}/{path}``; ``path`` is relative to DATA_DIR.

    ``direction`` ``in`` = from the client (``inbox_id``), ``out`` = from staff (``outbox_id``).
    ``thread_message_id`` is set once the message lands in a support thread."""

    __tablename__ = "support_attachments"

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    inbox_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    thread_message_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    outbox_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    direction: Mapped[str] = mapped_column(String(8))  # in / out
    # Legacy attachments remain unchanged. Newly received/sent files are ephemeral.
    ephemeral: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    remote_provider: Mapped[str | None] = mapped_column(String(24))
    remote_id: Mapped[str | None] = mapped_column(Text)
    remote_cover_id: Mapped[int | None] = mapped_column(Integer)
    remote_profile_id: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # photo document video voice audio image
    mime: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    original_name: Mapped[str] = mapped_column(String(256), default="")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=func.now())


class AvitoProfile(Base):
    """One Avito account connected from the site. ``client_secret_enc`` is Fernet-encrypted
    (AVITO_SECRET_KEY); the webhook secret itself is never stored, only its sha256."""

    __tablename__ = "avito_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(String(128), default="")
    client_id: Mapped[str] = mapped_column(String(128))
    client_secret_enc: Mapped[str] = mapped_column(Text)
    webhook_secret_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    account_id: Mapped[int | None] = mapped_column(BigInteger)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=func.now(), onupdate=func.now()
    )


class AvitoChat(Base):
    """A client's chat inside one Avito profile (the same person may write to several profiles)."""

    __tablename__ = "avito_chats"
    __table_args__ = (UniqueConstraint("profile_id", "chat_id", name="uq_avito_chats_profile_chat"),)

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(Integer, ForeignKey("avito_profiles.id"), index=True)
    chat_id: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Broadcast(Base):
    """A mass message created on the site and sent by the bot process (ТЗ-2, D.3).

    ``cover_ids`` empty = every enabled cover. ``cursor_user_id`` is the last
    processed client, so a restart continues where it stopped."""

    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    body: Mapped[str] = mapped_column(Text)
    image_path: Mapped[str | None] = mapped_column(Text)  # relative to DATA_DIR
    cover_ids: Mapped[list | None] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued running done cancelled failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    sent: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    # Why messages failed: {"reason": count}.
    fail_reasons: Mapped[dict | None] = mapped_column(JSON, default=dict)
    cursor_user_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupportTemplate(Base):
    __tablename__ = "support_templates"

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    updated_by: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), server_default=func.now(), onupdate=func.now())
