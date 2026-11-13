from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from aiohttp import web
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.db.base import session_factory
from app.db.models import Cover, Order, RobokassaPayment, SupportOutbox, SupportThread, User
from app.services.robokassa import valid_result_signature
from app.services.support_transport import queue_support_message

_FULFILLMENT = {
    "accepted",
    "bought",
    "shipping_to_ru",
    "customs",
    "shipping_in_ru",
    "awaiting_pickup",
    "received",
}


_REFUNDS = {"refund_pending", "refunded"}


async def _paid_after_cancel(session: AsyncSession, order: Order) -> web.Response:
    """ТЗ-2, B.4: money arrived for a cancelled order -> accept it and start a refund."""
    user = await session.get(User, order.user_id)
    if user is None:
        return web.Response(status=404, text="Customer not found")
    order.status = "refund_pending"
    order.paid_at = datetime.now(timezone.utc)
    order.paid_amount = order.price
    order.paid_staff_id = await session.scalar(
        select(SupportThread.assigned_to).where(SupportThread.user_id == order.user_id)
    )
    session.add(
        SupportOutbox(
            user_id=order.user_id,
            order_id=order.id,
            kind="status:refund_pending",
            body=f"📦 Заказ #{order.id}: новый статус — <b>{texts.STATUS_LABELS['refund_pending']}</b>",
            cover_id=order.cover_id,
        )
    )
    await queue_support_message(
        session,
        user,
        f"Оплата по отменённому заказу #{order.id} — нужен возврат ({order.price} ₽)",
        kind="payment_after_cancel",
        order_id=order.id,
        external_id=f"robokassa:{order.id}",
        cover=await session.get(Cover, order.cover_id) if order.cover_id else None,
    )
    await session.commit()
    return web.Response(text=f"OK{order.id}")


async def _process_unban_payment(
    session: AsyncSession,
    payment: RobokassaPayment,
    amount: Decimal,
    out_sum: str,
    inv_id: str,
    signature: str,
) -> web.Response:
    if amount != Decimal(payment.amount):
        return web.Response(status=400, text="Invalid payment amount")
    if not valid_result_signature(out_sum, inv_id, signature):
        return web.Response(status=403, text="Invalid signature")
    if payment.status == "paid":
        return web.Response(text=f"OK{payment.id}")
    if payment.status != "pending":
        return web.Response(status=409, text="Payment cannot be processed")
    user = await session.get(User, payment.user_id)
    if user is None:
        return web.Response(status=404, text="Customer not found")
    payment.status = "paid"
    payment.processed_at = func.now()
    user.is_banned = False
    session.add(
        SupportOutbox(
            user_id=user.id,
            order_id=None,
            kind="unbanned",
            body=texts.UNBANNED,
            cover_id=payment.cover_id,
        )
    )
    await session.commit()
    return web.Response(text=f"OK{payment.id}")


async def process_robokassa_result(params: dict[str, str]) -> web.Response:
    out_sum = params.get("OutSum", "")
    inv_id = params.get("InvId", "")
    signature = params.get("SignatureValue", "")
    if not out_sum or not inv_id or not signature:
        return web.Response(status=400, text="Missing payment parameters")
    try:
        order_id = int(inv_id)
        amount = Decimal(out_sum)
        if order_id <= 0 or not amount.is_finite() or amount <= 0:
            raise ValueError
    except (ValueError, InvalidOperation):
        return web.Response(status=400, text="Invalid payment parameters")

    async with session_factory() as session:
        unban_payment = await session.scalar(select(RobokassaPayment).where(RobokassaPayment.id == order_id).with_for_update().execution_options(populate_existing=True))
        if unban_payment is not None:
            return await _process_unban_payment(
                session, unban_payment, amount, out_sum, inv_id, signature
            )

        order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update().execution_options(populate_existing=True))
        if order is None:
            return web.Response(status=404, text="Order not found")
        if order.price is None or amount != Decimal(order.price):
            return web.Response(status=400, text="Invalid payment amount")
        if not valid_result_signature(out_sum, inv_id, signature):
            return web.Response(status=403, text="Invalid signature")
        if order.status == "approved" or order.status in _FULFILLMENT:
            return web.Response(text=f"OK{order.id}")
        if order.status in _REFUNDS and order.paid_at is not None:
            return web.Response(text=f"OK{order.id}")  # repeated notification
        if order.status == "cancelled":
            return await _paid_after_cancel(session, order)
        if order.status not in {"awaiting_payment", "payment_review"}:
            return web.Response(status=409, text="Order cannot be paid")

        user = await session.get(User, order.user_id)
        if user is None:
            return web.Response(status=404, text="Customer not found")
        order.status = "approved"
        order.paid_at = datetime.now(timezone.utc)
        order.paid_amount = order.price
        order.paid_staff_id = await session.scalar(
            select(SupportThread.assigned_to).where(SupportThread.user_id == order.user_id)
        )
        session.add(
            SupportOutbox(
                user_id=order.user_id,
                order_id=order.id,
                kind="approval",
                body=f"Оплата Robokassa по заказу #{order.id} подтверждена",
                cover_id=order.cover_id,
            )
        )
        await queue_support_message(
            session,
            user,
            f"Robokassa подтвердила оплату по заказу #{order.id} на сумму {order.price} ₽",
            kind="payment_success",
            order_id=order.id,
            external_id=f"robokassa:{order.id}",
            cover=await session.get(Cover, order.cover_id) if order.cover_id else None,
        )
        await session.commit()
        return web.Response(text=f"OK{order.id}")


async def handle_robokassa_result(request: web.Request) -> web.Response:
    params = dict(request.query)
    if request.method == "POST":
        params.update(await request.post())
    return await process_robokassa_result(params)
