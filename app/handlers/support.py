from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.assets import Banner
from app.db.models import User
from app.keyboards import inline
from app.services.navigation import show_screen
from app.states import UserState

router = Router(name="support")


@router.callback_query(F.data == "support:open")
async def open_support(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    user.current_order_id = None
    await repo.set_state(session, user, UserState.DIALOG)
    await show_screen(bot, session, user, Banner.PROFILE, texts.SUPPORT, inline.in_dialog())
    await call.answer()
