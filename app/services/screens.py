"""High-level screen renderers built on top of the navigation engine."""
from __future__ import annotations

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.assets import Banner
from app.db.models import User
from app.keyboards import inline
from app.services.navigation import show_screen
from app.states import UserState


async def show_menu(bot: Bot, session: AsyncSession, user: User) -> None:
    await repo.set_state(session, user, UserState.MENU)
    user.current_order_id = None
    await show_screen(bot, session, user, Banner.WELCOME, texts.WELCOME, inline.main_menu())


async def show_payment(bot: Bot, session: AsyncSession, user: User, order) -> None:
    caption = texts.PAYMENT.format(order_id=order.id, title=order.title, price=order.price)
    await show_screen(bot, session, user, Banner.PAYMENT, caption, inline.payment_actions(order.id))
