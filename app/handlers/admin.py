from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.assets import Banner
from app.config import get_settings
from app.db.models import OrderStatus, User
from app.keyboards import inline
from app.services.navigation import show_screen
from app.services.notify import relay_admin_to_user

router = Router(name="admin")

SUPPORT_TARGET = get_settings().support_target


def _is_admin(user: User) -> bool:
    return user.is_admin or user.id in get_settings().admin_ids


async def _decide(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, approve: bool
) -> None:
    if not _is_admin(user):
        await call.answer("Недостаточно прав", show_alert=True)
        return

    order_id = int(call.data.split(":")[2])
    order = await repo.get_order(session, order_id)
    if order is None:
        await call.answer("Заказ не найден", show_alert=True)
        return

    customer = await session.get(User, order.user_id)
    if customer is None:
        await call.answer("Клиент не найден", show_alert=True)
        return

    if approve:
        await repo.set_order_status(session, order, OrderStatus.APPROVED)
        await show_screen(
            bot,
            session,
            customer,
            Banner.APPROVED,
            texts.APPROVED.format(order_id=order.id),
            inline.open_dialog(order.id),
        )
        decision = "оплата подтверждена ✅"
    else:
        await repo.set_order_status(session, order, OrderStatus.REJECTED)
        await show_screen(
            bot,
            session,
            customer,
            Banner.REJECTED,
            texts.REJECTED_PAYMENT.format(order_id=order.id),
            inline.back_to_menu(),
        )
        decision = "заявка отклонена ❌"

    if call.message is not None:
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
    await call.answer(decision)
    target = get_settings().support_target
    if target is not None:
        await bot.send_message(
            target,
            texts.ADMIN_PAYMENT_DECISION.format(order_id=order.id, decision=decision),
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("adm:confirm:"))
async def confirm(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    await _decide(call, bot, session, user, approve=True)


@router.callback_query(F.data.startswith("adm:reject:"))
async def reject(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    await _decide(call, bot, session, user, approve=False)


@router.message(F.reply_to_message, F.chat.id == SUPPORT_TARGET)
async def admin_reply(message: Message, bot: Bot, session: AsyncSession) -> None:
    """Relay an admin's reply (in the support chat) back to the customer."""
    if SUPPORT_TARGET is None:
        return
    user_id = await repo.resolve_relay(session, message.reply_to_message.message_id)
    if user_id is None:
        return
    await relay_admin_to_user(bot, user_id, message)
