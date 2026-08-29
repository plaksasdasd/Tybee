from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.services.notify import ensure_user_thread
from app.services.screens import show_menu

router = Router(name="start")
logger = logging.getLogger(__name__)


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot, session: AsyncSession, user: User) -> None:
    await show_menu(bot, session, user, force_new=True)
    # Сразу создаём ветку клиента в саппорт-чате (как в rayabot),
    # без ожидания первого обращения в поддержку.
    try:
        await ensure_user_thread(bot, session, user)
    except Exception:
        logger.exception("Could not ensure support thread on /start for user %s", user.id)


@router.callback_query(F.data == "menu:open")
async def open_menu(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    await show_menu(bot, session, user)
    await call.answer()


@router.callback_query(F.data == "back:menu")
async def back_menu(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    """«Назад» to the main menu (previous step for the order-choice screen)."""
    await show_menu(bot, session, user)
    await call.answer()
