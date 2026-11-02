from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiohttp import web

from app.db.base import session_factory
from app.db.models import Order, SupportOutbox, User
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
