from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.covers.loader import cover_text
from app.db.models import User, UserCover
from app.services.screens import show_payment

router = Router(name="payment")


@router.callback_query(F.data.startswith("pay:proceed:"))
async def proceed(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, uc: UserCover
) -> None:
    order_id = int(call.data.split(":")[2])
    order = await repo.get_order(session, order_id)
    if order is None or order.user_id != user.id:
        await call.answer(cover_text(uc.cover_id, "ANSWER_ORDER_NOT_FOUND"), show_alert=True)
        return
    await show_payment(bot, session, user, uc, order)
    await call.answer()
