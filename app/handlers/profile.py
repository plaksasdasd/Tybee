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

router = Router(name="profile")

_STATUS_LABELS = {
    OrderStatus.NEW.value: "новый",
    OrderStatus.AWAITING_PAYMENT.value: "ожидает оплаты",
    OrderStatus.PAYMENT_REVIEW.value: "проверка оплаты",
    OrderStatus.APPROVED.value: "одобрен",
    OrderStatus.REJECTED.value: "отклонён",
    OrderStatus.IN_DIALOG.value: "в работе",
}


@router.callback_query(F.data == "profile:open")
async def open_profile(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    orders = await repo.list_orders(session, user.id)
    caption = texts.PROFILE.format(
        name=repo.display_name(user) or "Клиент",
        user_id=user.id,
        orders_count=len(orders),
    )
    await show_screen(bot, session, user, Banner.PROFILE, caption, inline.profile_menu())
    await call.answer()


@router.callback_query(F.data == "profile:offer")
async def show_offer(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    await show_screen(bot, session, user, Banner.PROFILE, texts.OFFER, inline.back_to_profile())
    await call.answer()


@router.callback_query(F.data == "profile:orders")
async def show_orders(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    orders = await repo.list_orders(session, user.id)
    if not orders:
        await show_screen(
            bot, session, user, Banner.PROFILE, texts.NO_ORDERS, inline.back_to_profile()
        )
        await call.answer()
        return

    lines = [texts.ORDERS_HEADER]
    for order in orders:
        title = order.title or "Персональная заявка"
        status = _STATUS_LABELS.get(order.status, order.status)
        price = f" — {order.price} ₽" if order.price else ""
        lines.append(f"<b>#{order.id}</b> {title}{price}\n   └ статус: {status}")
    await show_screen(
        bot, session, user, Banner.PROFILE, "\n".join(lines), inline.back_to_profile()
    )
    await call.answer()
