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
from app.services.screens import show_menu
from app.states import UserState

router = Router(name="dialog")


@router.callback_query(F.data.startswith("dialog:open:"))
async def open_dialog(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    order_id = int(call.data.split(":")[2])
    order = await repo.get_order(session, order_id)
    if order is not None and order.user_id == user.id:
        await repo.set_order_status(session, order, OrderStatus.IN_DIALOG)
        user.current_order_id = order.id
    await repo.set_state(session, user, UserState.DIALOG)
    await show_screen(bot, session, user, Banner.APPROVED, texts.DIALOG_OPEN, inline.in_dialog())
    await call.answer()


@router.callback_query(F.data == "dialog:close")
async def close_dialog(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    await show_menu(bot, session, user)
    await call.answer("Диалог завершён")
