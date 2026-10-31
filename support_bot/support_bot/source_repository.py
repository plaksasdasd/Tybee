from __future__ import annotations

import secrets

from sqlalchemy import update

from support_bot.models import SupportRequest
from support_bot.source_db import source_session_factory
from support_bot.source_models import SourceConversation, SourceOrder, SourceOutbox, SourceUser

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


def _factory():
    if source_session_factory is None:
        raise RuntimeError("Source database is not configured")
    return source_session_factory()


async def queue_source_outbox(
    request: SupportRequest,
    body: str,
    *,
    kind: str = "text",
    order_id: int | None = None,
) -> None:
    if request.source != "main":
        return
    async with _factory() as session:
        session.add(
            SourceOutbox(
                user_id=request.customer_id,
                order_id=order_id,
                kind=kind,
                body=body,
            )
        )
        await session.commit()


async def create_order(
    request: SupportRequest, title: str, description: str, price: int
) -> SourceOrder:
    async with _factory() as session:
        user = await session.get(SourceUser, request.customer_id)
        if user is None:
            raise ValueError("Клиент не найден")
        while True:
            order_id = secrets.randbelow(9_900_000_000) + 100_000_000
            if await session.get(SourceOrder, order_id) is None:
                break
        order = SourceOrder(
            id=order_id,
            user_id=user.id,
            type="personal",
            status="awaiting_payment",
            title=title,
            description=description or None,
            price=price,
        )
        session.add(order)
        user.current_order_id = order.id
        session.add(
            SourceOutbox(
                user_id=user.id,
                order_id=order.id,
                kind="invoice",
                body=f"Счёт по заказу #{order.id}",
            )
        )
        await session.commit()
        return order


async def decide_payment(order_id: int, approved: bool) -> tuple[SourceOrder | None, str | None]:
    async with _factory() as session:
        result = await session.execute(
            update(SourceOrder)
            .where(
                SourceOrder.id == order_id,
                SourceOrder.status.in_(["payment_review", "awaiting_payment"]),
            )
            .values(status="approved" if approved else "rejected")
            .returning(SourceOrder)
        )
        order = result.scalar_one_or_none()
        if order is None:
            current = await session.get(SourceOrder, order_id)
            return current, "По этому заказу уже принято решение" if current else "Заказ не найден"
        if approved:
            body = f"✅ Оплата по заказу #{order.id} подтверждена."
            kind = "approval"
        else:
            body = f"❎ По заказу #{order.id} оплата не подтверждена. Напишите в поддержку, если платёж был отправлен."
            kind = "text"
        session.add(SourceOutbox(user_id=order.user_id, order_id=order.id, kind=kind, body=body))
        await session.commit()
        return order, None


async def change_order_status(order_id: int, status: str) -> tuple[SourceOrder | None, str | None]:
    if status not in STATUS_LABELS:
        return None, "Неизвестный статус"
    async with _factory() as session:
        order = await session.get(SourceOrder, order_id)
        if order is None:
            return None, "Заказ не найден"
        allowed = {"approved", *STATUS_LABELS}
        if order.status not in allowed:
            return order, "Сначала подтвердите оплату заказа"
        order.status = status
        label = STATUS_LABELS[status]
        session.add(
            SourceOutbox(
                user_id=order.user_id,
                order_id=order.id,
                kind="text",
                body=f"📦 Заказ #{order.id}: новый статус — <b>{label}</b>",
            )
        )
        await session.commit()
        return order, None


async def set_client_banned(request: SupportRequest, banned: bool) -> SourceUser:
    async with _factory() as session:
        user = await session.get(SourceUser, request.customer_id)
        if user is None:
            raise ValueError("Клиент не найден")
        user.is_banned = banned
        await session.commit()
        return user


async def close_source_request(request: SupportRequest) -> None:
    if request.source != "main":
        return
    async with _factory() as session:
        conversation = await session.get(SourceConversation, request.source_conversation_id)
        if conversation is not None:
            conversation.status = "closed"
        session.add(
            SourceOutbox(
                user_id=request.customer_id,
                order_id=request.source_order_id,
                kind="text",
                body="Диалог с поддержкой завершён.",
            )
        )
        await session.commit()
