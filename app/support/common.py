from __future__ import annotations

from datetime import datetime, timezone

FULFILLMENT_STATUSES: tuple[tuple[str, str], ...] = (
    ("accepted", "📥 Принят"),
    ("bought", "🛍 Выкуплен"),
    ("shipping_to_ru", "✈️ Доставляется в Россию"),
    ("customs", "🛃 На таможне"),
    ("shipping_in_ru", "🚚 Доставляется по России"),
    ("awaiting_pickup", "📬 Ожидает получения"),
    ("received", "✅ Получен"),
)
STATUS_LABELS = dict(FULFILLMENT_STATUSES)

# All 16 order statuses in UI order: the 13 original ones, then refunds/cancel (ТЗ-2, B.1).
ORDER_STATUSES: tuple[tuple[str, str], ...] = (
    ("new", "🆕 Новый"),
    ("awaiting_payment", "💸 Ожидает оплаты"),
    ("payment_review", "⏳ Проверка оплаты"),
    ("approved", "✅ Оплата подтверждена"),
    ("rejected", "❎ Отклонён"),
    ("in_dialog", "💬 В работе"),
    *FULFILLMENT_STATUSES,
    ("refund_pending", "↩️ Оформляется возврат"),
    ("refunded", "💸 Возвращён"),
    ("cancelled", "❌ Отменён"),
)
ORDER_STATUS_LABELS = dict(ORDER_STATUSES)

DELIVERY = tuple(code for code, _ in FULFILLMENT_STATUSES)
UNPAID = ("new", "awaiting_payment", "payment_review", "in_dialog", "rejected")
PAID = ("approved", *DELIVERY)
# Orders that are finished: no /ping, not "stale".
FINAL_STATUSES = frozenset({"received", "refunded", "cancelled", "rejected"})
NO_PING_STATUSES = FINAL_STATUSES
# Statuses staff can set from the site.
SETTABLE = frozenset({*DELIVERY, "refund_pending", "refunded", "cancelled"})

# Roles that take auto-assigned threads.
ASSIGNABLE_ROLES = ("client_manager_1", "client_manager_2")


class SupportError(Exception):
    def __init__(self, message: str, status: int = 409, *, retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def now() -> datetime:
    return datetime.now(timezone.utc)


def ts(value: datetime | None) -> float:
    """Comparable timestamp; naive datetimes (SQLite) are treated as UTC."""
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()
