from __future__ import annotations

import html

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.assets import Banner
from app.covers.loader import cover_text
from app.db.models import OrderStatus, OrderType, User, UserCover
from app.keyboards import inline
from app.services.navigation import show_screen
from app.states import UserState

router = Router(name="mystery")


async def _render_boxes(bot: Bot, session: AsyncSession, user: User, uc: UserCover) -> None:
    await repo.set_state(session, uc, UserState.MENU)
    cid = uc.cover_id
    boxes = await repo.list_available_boxes(session)
    if boxes:
        await show_screen(
            bot,
            session,
            user,
            uc,
            Banner.MYSTERY,
            cover_text(cid, "MYSTERY_LIST"),
            inline.mystery_boxes(cid, boxes),
        )
    else:
        await show_screen(
            bot,
            session,
            user,
            uc,
            Banner.MYSTERY,
            cover_text(cid, "MYSTERY_EMPTY"),
            inline.mystery_empty(cid),
        )


@router.callback_query(F.data == "order:mystery")
async def show_boxes(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, uc: UserCover
) -> None:
    await call.answer()  # stop the button spinner before the slow screen edit
    await _render_boxes(bot, session, user, uc)


@router.callback_query(F.data.startswith("mystery:item:"))
async def pick_box(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, uc: UserCover
) -> None:
    cid = uc.cover_id
    box = await repo.get_box(session, int(call.data.split(":")[2]))
    if box is None or not box.available:
        await call.answer(cover_text(cid, "ANSWER_BOX_UNAVAILABLE"), show_alert=True)
        await _render_boxes(bot, session, user, uc)
        return
    await call.answer()
    await repo.set_state(session, uc, UserState.MENU)
    order = await repo.create_order(
        session,
        user,
        uc,
        type=OrderType.MYSTERY_BOX.value,
        status=OrderStatus.AWAITING_PAYMENT.value,
        title=box.title,
        price=box.price,
    )
    caption = cover_text(cid, "MYSTERY_OFFER", title=html.escape(box.title), price=box.price)
    await show_screen(
        bot,
        session,
        user,
        uc,
        Banner.APPROVED,
        caption,
        inline.pay_offer(cid, order.id),
        force_new=True,
    )
