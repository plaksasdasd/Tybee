from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.assets import Banner
from app.covers.loader import cover_text
from app.db.models import User, UserCover
from app.keyboards import inline
from app.services.navigation import show_screen
from app.services.screens import show_menu
from app.services.support_transport import close_support_dialog
from app.states import UserState

router = Router(name="dialog")


@router.callback_query(F.data == "reminder:delete")
async def delete_reminder(call: CallbackQuery, bot: Bot) -> None:
    await call.answer()
    if call.message is not None:
        await bot.delete_message(chat_id=call.message.chat.id, message_id=call.message.message_id)


@router.callback_query(F.data.startswith("dialog:open:"))
async def open_dialog(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, uc: UserCover
) -> None:
    await call.answer()  # stop the button spinner before the slow screen edit
    order_id = int(call.data.split(":")[2])
    order = await repo.get_order(session, order_id)
    if order is not None and order.user_id == user.id:
        uc.current_order_id = order.id
    await repo.set_state(session, uc, UserState.DIALOG)
    cid = uc.cover_id
    await show_screen(
        bot, session, user, uc, Banner.APPROVED, cover_text(cid, "DIALOG_OPEN"), inline.in_dialog(cid)
    )


@router.callback_query(F.data == "dialog:close")
async def close_dialog(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, uc: UserCover
) -> None:
    await call.answer(cover_text(uc.cover_id, "ANSWER_DIALOG_CLOSED"))
    await close_support_dialog(
        session, user, order_id=uc.current_order_id, external_id=f"telegram:close:{user.id}:{call.id}"
    )
    await repo.set_state(session, uc, UserState.MENU)
    await show_menu(bot, session, user, uc)
