"""Client-side unban flow with Robokassa payments."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.covers.loader import cover_text, cover_unban_fine
from app.db.models import User, UserCover
from app.keyboards import inline
from app.services.robokassa import build_payment_link, create_unban_payment, robokassa_ready

router = Router(name="ban")


@router.callback_query(F.data == "unban:pay")
async def request_unban_payment(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, uc: UserCover
) -> None:
    cid = uc.cover_id
    if not user.is_banned:
        await call.answer(cover_text(cid, "ANSWER_BAN_LIFTED"), show_alert=True)
        return
    if not robokassa_ready():
        await call.answer(cover_text(cid, "ANSWER_PAYMENT_UNAVAILABLE"), show_alert=True)
        return
    payment = await create_unban_payment(session, user, cover_id=cid, amount=cover_unban_fine(cid))
    url = build_payment_link(
        payment.id,
        payment.amount,
        cover_text(cid, "UNBAN_PAYMENT_DESCRIPTION", user_id=user.id),
    )
    if url is None:
        await call.answer(cover_text(cid, "ANSWER_PAYMENT_UNAVAILABLE"), show_alert=True)
        return
    await bot.send_message(
        user.id,
        cover_text(cid, "UNBAN_PAYMENT_MESSAGE", amount=payment.amount),
        parse_mode="HTML",
        reply_markup=inline.pay_fine_url(cid, url, payment.amount),
    )
    await call.answer()
