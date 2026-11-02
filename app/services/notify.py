"""Support-queue events for personal requests."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, User
from app.services.support_transport import queue_support_message


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
