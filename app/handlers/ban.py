"""Client-side unban flow with Robokassa payments."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.keyboards import inline
from app.services.robokassa import build_payment_link, create_unban_payment, robokassa_ready

router = Router(name="ban")


@router.callback_query(F.data == "unban:pay")
async def request_unban_payment(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User
) -> None:
    if not user.is_banned:
        await call.answer("Блокировка уже снята.", show_alert=True)
        return
    if not robokassa_ready():
        await call.answer("Оплата временно недоступна.", show_alert=True)
        return
    payment = await create_unban_payment(session, user)
    url = build_payment_link(
        payment.id,
        payment.amount,
        f"Разблокировка аккаунта Тайби #{user.id}",
    )
    if url is None:
        await call.answer("Оплата временно недоступна.", show_alert=True)
        return
    await bot.send_message(
        user.id,
        f"Для снятия блокировки оплатите штраф <b>{payment.amount} ₽</b> через Robokassa. "
        "После подтверждения платежа доступ будет восстановлен автоматически.",
        parse_mode="HTML",
        reply_markup=inline.pay_fine_url(url, payment.amount),
    )
    await call.answer()
