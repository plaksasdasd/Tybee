from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.assets import Banner
from app.covers.loader import cover_text
from app.db.models import Cover, OrderStatus, OrderType, User, UserCover
from app.keyboards import inline
from app.services.navigation import show_screen
from app.services.notify import notify_new_personal
from app.states import UserState

router = Router(name="personal")


@router.callback_query(F.data == "order:personal")
async def start_personal(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, uc: UserCover
) -> None:
    await call.answer()  # stop the button spinner before the slow screen edit
    await repo.set_state(session, uc, UserState.AWAIT_PERSONAL)
    cid = uc.cover_id
    await show_screen(
        bot,
        session,
        user,
        uc,
        Banner.ORDER_CHOICE,
        cover_text(cid, "PERSONAL_PROMPT"),
        inline.back_button(cid, "order:start"),
    )


async def process_personal(
    bot: Bot, session: AsyncSession, user: User, uc: UserCover, cover: Cover, description: str
) -> None:
    order = await repo.create_order(
        session,
        user,
        uc,
        type=OrderType.PERSONAL.value,
        status=OrderStatus.IN_DIALOG.value,
        description=description,
    )
    await repo.set_state(session, uc, UserState.DIALOG)
    cid = uc.cover_id
    caption = cover_text(cid, "PERSONAL_DONE", order_id=order.id)
    await show_screen(
        bot, session, user, uc, Banner.APPROVED, caption, inline.in_dialog(cid), force_new=True
    )
    await notify_new_personal(session, user, order, cover)
