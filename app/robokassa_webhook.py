from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiohttp import web
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.db.base import session_factory
from app.db.models import Order, RobokassaPayment, SupportOutbox, User
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
            kind="text",
            body=texts.UNBANNED,
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
    except (ValueError, InvalidOperation):
        return web.Response(status=400, text="Invalid payment parameters")

    async with session_factory() as session:
        unban_payment = await session.get(RobokassaPayment, order_id)
        if unban_payment is not None:
            return await _process_unban_payment(
                session, unban_payment, amount, out_sum, inv_id, signature
            )

        order = await session.get(Order, order_id)
        if order is None:
            return web.Response(status=404, text="Order not found")
        if order.price is None or amount != Decimal(order.price):
            return web.Response(status=400, text="Invalid payment amount")
        if not valid_result_signature(out_sum, inv_id, signature):
            return web.Response(status=403, text="Invalid signature")
        if order.status == "approved" or order.status in _FULFILLMENT:
            return web.Response(text=f"OK{order.id}")
        if order.status not in {"awaiting_payment", "payment_review"}:
            return web.Response(status=409, text="Order cannot be paid")

        user = await session.get(User, order.user_id)
        if user is None:
            return web.Response(status=404, text="Customer not found")
        order.status = "approved"
        session.add(
            SupportOutbox(
                user_id=order.user_id,
                order_id=order.id,
                kind="approval",
                body=f"Оплата Robokassa по заказу #{order.id} подтверждена",
            )
        )
        await queue_support_message(
            session,
            user,
            f"Robokassa подтвердила оплату по заказу #{order.id} на сумму {order.price} ₽",
            kind="payment_success",
            order_id=order.id,
            external_id=f"robokassa:{order.id}",
        )
        await session.commit()
        return web.Response(text=f"OK{order.id}")


async def handle_robokassa_result(request: web.Request) -> web.Response:
    params = dict(request.query)
    if request.method == "POST":
        params.update(await request.post())
    return await process_robokassa_result(params)
