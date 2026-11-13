"""Copy that stays in code.

* Status labels used by service code.
* The few client-facing strings used ONLY for Avito clients (Avito is not a
  cover and has no config.yaml): ``PAYMENT_UNAVAILABLE``, ``AVITO_INVOICE``,
  ``UNBANNED``.

Everything a Telegram client sees lives in the cover's ``config.yaml``
(see ``app/covers``).
"""
from __future__ import annotations


PAYMENT_UNAVAILABLE = (
    "Оплата через Robokassa временно недоступна. Попробуйте позже."
)

AVITO_INVOICE = (
    "🧾 ВАМ ВЫСТАВЛЕН СЧЁТ\n\n"
    "🛍 {title}\n"
    "{description}"
    "💴 К оплате: <b>{price} ₽</b>\n\n"
    "После оплаты через Robokassa подтверждение придёт автоматически."
)

UNBANNED = (
    "✅ <b>Блокировка снята</b>\n\n"
    "Спасибо! Доступ к сервису восстановлен. Нажмите /start, чтобы вернуться в меню."
)

STATUS_LABELS = {
    "new": "🆕 Новый",
    "awaiting_payment": "💸 Ожидает оплаты",
    "payment_review": "⏳ Проверка оплаты",
    "approved": "✅ Оплата подтверждена",
    "rejected": "❎ Отклонён",
    "in_dialog": "💬 В работе",
    "accepted": "📥 Принят",
    "bought": "🛍 Выкуплен",
    "shipping_to_ru": "✈️ Доставляется в Россию",
    "customs": "🛃 На таможне",
    "shipping_in_ru": "🚚 Доставляется по России",
    "awaiting_pickup": "📬 Ожидает получения",
    "received": "✅ Получен",
    "refund_pending": "↩️ Оформляется возврат",
    "refunded": "💸 Возвращён",
    "cancelled": "❌ Отменён",
}

AVITO_PING = "🔔 Напоминаем о заказе #{order_id} «{title}». Текущий статус: {status}."
