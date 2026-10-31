"""Support-queue events and payment settings."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.config import get_settings
from app.db.models import Order, User
from app.services.support_transport import queue_support_message


def requisites() -> str:
    return get_settings().payment_requisites.strip() or texts.DEFAULT_REQUISITES


async def notify_new_payment(session: AsyncSession, user: User, order: Order) -> None:
    body = (
        f"Платёж на проверку по заказу #{order.id}\n"
        f"Товар: {order.title or '—'}\n"
        f"Артикул: {order.article or '—'}\n"
        f"Сумма: {order.price} ₽"
    )
    await queue_support_message(
        session,
        user,
        body,
        kind="payment_review",
        order_id=order.id,
        external_id=f"payment:{order.id}",
    )


async def notify_new_personal(session: AsyncSession, user: User, order: Order) -> None:
    body = f"Персональная заявка по заказу #{order.id}\n\n{order.description or ''}"
    await queue_support_message(
        session,
        user,
        body,
        kind="personal",
        order_id=order.id,
        external_id=f"personal:{order.id}",
    )
