"""Client-side unban flow with replay-safe Stars payments and refunds."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.config import get_settings
from app.db.models import PaymentCharge, User
from app.services.screens import show_menu

logger = logging.getLogger(__name__)
router = Router(name="ban")


def _payload(user_id: int) -> str:
    return f"unban_fine:{user_id}"


@router.callback_query(F.data == "unban:pay")
async def request_unban_invoice(call: CallbackQuery, bot: Bot, user: User) -> None:
    fine = get_settings().unban_fine_stars
    await bot.send_invoice(
        chat_id=user.id,
        title=texts.UNBAN_INVOICE_TITLE,
        description=texts.UNBAN_INVOICE_DESC,
        payload=_payload(user.id),
        currency="XTR",
        prices=[LabeledPrice(label=texts.UNBAN_INVOICE_LABEL, amount=fine)],
    )
    await call.answer()


@router.pre_checkout_query()
async def confirm_pre_checkout(
    query: PreCheckoutQuery, session: AsyncSession, user: User
) -> None:
    if not user.is_banned:
        await query.answer(ok=False, error_message="Блокировка уже снята.")
        return
    if query.invoice_payload != _payload(user.id):
        await query.answer(ok=False, error_message="Некорректный платёж.")
        return
    if query.currency != "XTR" or query.total_amount != get_settings().unban_fine_stars:
        await query.answer(ok=False, error_message="Некорректная сумма платежа.")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(
    message: Message, bot: Bot, session: AsyncSession, user: User
) -> None:
    payment = message.successful_payment
    if payment.invoice_payload != _payload(user.id):
        return
    if payment.currency != "XTR" or payment.total_amount != get_settings().unban_fine_stars:
        return
    existing = await session.scalar(
        select(PaymentCharge).where(
            PaymentCharge.telegram_payment_charge_id == payment.telegram_payment_charge_id
        )
    )
    if existing is not None:
        return
    session.add(
        PaymentCharge(
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
            user_id=user.id,
        )
    )
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return
    await repo.set_banned(session, user, False)
    await message.answer(texts.UNBANNED, parse_mode="HTML")
    await show_menu(bot, session, user, force_new=True)


@router.message(F.refunded_payment)
async def on_refunded_payment(message: Message, session: AsyncSession, user: User) -> None:
    payment = message.refunded_payment
    if payment.invoice_payload != _payload(user.id):
        return
    charge = await session.scalar(
        select(PaymentCharge).where(
            PaymentCharge.telegram_payment_charge_id == payment.telegram_payment_charge_id
        )
    )
    if charge is None or charge.user_id != user.id or charge.refunded_at is not None:
        return
    charge.refunded_at = func.now()
    await repo.set_banned(session, user, True)
    await message.answer("Возврат платежа зарегистрирован. Доступ к боту снова ограничен.")
