from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.assets import Banner
from app.db.models import OrderStatus, User
from app.keyboards import inline
from app.services.navigation import show_screen
from app.services.notify import notify_new_payment
from app.services.screens import show_payment

router = Router(name="payment")


@router.callback_query(F.data.startswith("pay:proceed:"))
async def proceed(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    order_id = int(call.data.split(":")[2])
    order = await repo.get_order(session, order_id)
    if order is None or order.user_id != user.id:
        await call.answer("Заказ не найден", show_alert=True)
        return
    await show_payment(bot, session, user, order)
    await call.answer()


@router.callback_query(F.data.startswith("pay:done:"))
async def paid(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    order_id = int(call.data.split(":")[2])
    order = await repo.get_order(session, order_id)
    if order is None or order.user_id != user.id:
        await call.answer("Заказ не найден", show_alert=True)
        return

    await repo.set_order_status(session, order, OrderStatus.PAYMENT_REVIEW)
    caption = texts.PAYMENT_REVIEW.format(order_id=order.id)
    await show_screen(bot, session, user, Banner.ALMOST, caption, inline.review_pending())
    await notify_new_payment(bot, session, user, order)
    await call.answer("Передали на проверку ✅")
