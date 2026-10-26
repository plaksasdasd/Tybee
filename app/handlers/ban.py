"""Client-side unban flow: pay a fine in Telegram Stars (XTR) to get unblocked."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.config import get_settings
from app.db.models import User
from app.services.screens import show_menu

router = Router(name="ban")

UNBAN_PAYLOAD = "unban_fine"


@router.callback_query(F.data == "unban:pay")
async def request_unban_invoice(call: CallbackQuery, bot: Bot, user: User) -> None:
    fine = get_settings().unban_fine_stars
    await bot.send_invoice(
        chat_id=user.id,
        title=texts.UNBAN_INVOICE_TITLE,
        description=texts.UNBAN_INVOICE_DESC,
        payload=UNBAN_PAYLOAD,
        currency="XTR",
        prices=[LabeledPrice(label=texts.UNBAN_INVOICE_LABEL, amount=fine)],
    )
    await call.answer()


@router.pre_checkout_query()
async def confirm_pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(
    message: Message, bot: Bot, session: AsyncSession, user: User
) -> None:
    if message.successful_payment.invoice_payload != UNBAN_PAYLOAD:
        return
    await repo.set_banned(session, user, False)
    await message.answer(texts.UNBANNED, parse_mode="HTML")
    await show_menu(bot, session, user, force_new=True)
