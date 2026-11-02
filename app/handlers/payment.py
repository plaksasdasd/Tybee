from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.db.models import User
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
