from __future__ import annotations

import html

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.assets import Banner
from app.covers.loader import cover_status_title, cover_text
from app.db.models import User, UserCover
from app.keyboards import inline
from app.services.navigation import show_screen

router = Router(name="profile")


@router.callback_query(F.data == "profile:open")
async def open_profile(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, uc: UserCover
) -> None:
    await call.answer()  # stop the button spinner before the slow screen edit
    cid = uc.cover_id
    orders_count = await repo.count_user_orders(session, user.id, cid)
    caption = cover_text(
        cid,
        "PROFILE",
        name=repo.display_name(user) or cover_text(cid, "PROFILE_DEFAULT_NAME"),
        user_id=user.id,
        orders_count=orders_count,
    )
    await show_screen(bot, session, user, uc, Banner.PROFILE, caption, inline.profile_menu(cid))


@router.callback_query(F.data == "profile:offer")
async def show_offer(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, uc: UserCover
) -> None:
    await call.answer()  # stop the button spinner before the slow screen edit
    cid = uc.cover_id
    await show_screen(
        bot, session, user, uc, Banner.PROFILE, cover_text(cid, "OFFER"), inline.back_to_profile(cid)
    )


@router.callback_query(F.data == "profile:orders")
async def show_orders(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, uc: UserCover
) -> None:
    await call.answer()  # stop the button spinner before the slow screen edit
    cid = uc.cover_id
    orders = await repo.list_orders(session, user.id, cid)
    if not orders:
        await show_screen(
            bot,
            session,
            user,
            uc,
            Banner.PROFILE,
            cover_text(cid, "NO_ORDERS"),
            inline.back_to_profile(cid),
        )
        return

    lines = [cover_text(cid, "ORDERS_HEADER")]
    for order in orders:
        title = html.escape(order.title or cover_text(cid, "ORDER_DEFAULT_TITLE"))
        status = cover_status_title(cid, order.status).capitalize()
        price_part = (
            cover_text(cid, "ORDER_PRICE_PART", price=order.price) if order.price else ""
        )
        lines.append(
            cover_text(
                cid,
                "ORDER_LINE",
                order_id=order.id,
                title=title,
                price_part=price_part,
                status=status,
            )
        )
    await show_screen(
        bot, session, user, uc, Banner.PROFILE, "\n".join(lines), inline.back_to_profile(cid)
    )
