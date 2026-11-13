from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.assets import Banner
from app.covers.loader import cover_text
from app.db.models import User, UserCover
from app.keyboards import inline
from app.services.navigation import show_screen
from app.states import UserState

router = Router(name="support")


@router.callback_query(F.data == "support:open")
async def open_support(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, uc: UserCover
) -> None:
    await call.answer()  # stop the button spinner before the slow screen edit
    uc.current_order_id = None
    await repo.set_state(session, uc, UserState.DIALOG)
    cid = uc.cover_id
    await show_screen(
        bot, session, user, uc, Banner.PROFILE, cover_text(cid, "SUPPORT"), inline.in_dialog(cid)
    )
