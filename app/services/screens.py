"""High-level screen renderers built on top of the navigation engine."""
from __future__ import annotations

import html

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.assets import Banner
from app.db.models import User
from app.keyboards import inline
from app.services.navigation import show_screen
from app.services import notify
from app.services.notify import requisites
from app.states import UserState


async def show_menu(
    bot: Bot, session: AsyncSession, user: User, *, force_new: bool = False
) -> None:
    await repo.set_state(session, user, UserState.MENU)
    user.current_order_id = None
    await show_screen(
        bot, session, user, Banner.WELCOME, texts.WELCOME, inline.main_menu(), force_new=force_new
    )


async def show_payment(bot: Bot, session: AsyncSession, user: User, order) -> None:
    caption = texts.PAYMENT.format(
        order_id=order.id,
        title=html.escape(order.title or ""),
        price=order.price,
        requisites=requisites(),
    )
    await show_screen(bot, session, user, Banner.PAYMENT, caption, inline.payment_actions(order.id))


async def send_order_invoice(bot: Bot, session: AsyncSession, user: User, order) -> None:
    """Push a support-created order's invoice to the client as a fresh screen.

    Avito clients get a plain Avito message instead (plus a confirm/reject
    panel in their support thread — see notify.send_avito_invoice).
    """
    description = f"{html.escape(order.description)}\n" if order.description else ""
    if user.source == "avito":
        await notify.send_avito_invoice(bot, session, user, order)
        return
    caption = texts.ORDER_CREATED.format(
        title=html.escape(order.title or ""),
        description=description,
        price=order.price,
        requisites=requisites(),
    )
    await show_screen(
        bot,
        session,
        user,
        Banner.PAYMENT,
        caption,
        inline.payment_actions(order.id),
        force_new=True,
    )
