from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.assets import Banner
from app.db.models import OrderStatus, OrderType, User
from app.keyboards import inline
from app.services.navigation import show_screen
from app.services.notify import notify_new_personal
from app.states import UserState

router = Router(name="personal")


@router.callback_query(F.data == "order:personal")
async def start_personal(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    await repo.set_state(session, user, UserState.AWAIT_PERSONAL)
    await show_screen(
        bot, session, user, Banner.SEARCH, texts.PERSONAL_PROMPT, inline.cancel_to_menu()
    )
    await call.answer()


async def process_personal(bot: Bot, session: AsyncSession, user: User, description: str) -> None:
    order = await repo.create_order(
        session,
        user,
        type=OrderType.PERSONAL.value,
        status=OrderStatus.IN_DIALOG.value,
        description=description,
    )
    await repo.set_state(session, user, UserState.DIALOG)
    caption = texts.PERSONAL_DONE.format(order_id=order.id)
    await show_screen(
        bot, session, user, Banner.APPROVED, caption, inline.in_dialog(), force_new=True
    )
    await notify_new_personal(bot, session, user, order)
