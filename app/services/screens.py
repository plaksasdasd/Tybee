"""High-level screen renderers built on top of the navigation engine."""
from __future__ import annotations

import html

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.assets import Banner
from app.covers.loader import cover_text
from app.db.models import User, UserCover
from app.keyboards import inline
from app.services.navigation import show_screen
from app.services.robokassa import payment_link
from app.states import UserState


async def show_menu(
    bot: Bot, session: AsyncSession, user: User, uc: UserCover, *, force_new: bool = False
) -> None:
    await repo.set_state(session, uc, UserState.MENU)
    uc.current_order_id = None
    cid = uc.cover_id
    await show_screen(
        bot,
        session,
        user,
        uc,
        Banner.WELCOME,
        cover_text(cid, "WELCOME"),
        inline.main_menu(cid),
        force_new=force_new,
    )


async def show_payment(bot: Bot, session: AsyncSession, user: User, uc: UserCover, order) -> None:
    cid = uc.cover_id
    url = payment_link(order)
    if url is None:
        await show_screen(
            bot,
            session,
            user,
            uc,
            Banner.PAYMENT,
            cover_text(cid, "PAYMENT_UNAVAILABLE"),
            inline.back_to_menu(cid),
        )
        return
    caption = cover_text(
        cid,
        "PAYMENT",
        order_id=order.id,
        title=html.escape(order.title or ""),
        price=order.price,
    )
    await show_screen(
        bot, session, user, uc, Banner.PAYMENT, caption, inline.payment_actions(cid, url)
    )
