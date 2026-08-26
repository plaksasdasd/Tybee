from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.services.screens import show_menu

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot, session: AsyncSession, user: User) -> None:
    await show_menu(bot, session, user, force_new=True)


@router.callback_query(F.data == "menu:open")
async def open_menu(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    await show_menu(bot, session, user)
    await call.answer()


@router.callback_query(F.data == "back:menu")
async def back_menu(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    """«Назад» to the main menu (previous step for the order-choice screen)."""
    await show_menu(bot, session, user)
    await call.answer()
