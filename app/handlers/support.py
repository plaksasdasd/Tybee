from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.db.models import User
from app.keyboards import inline
from app.services.navigation import show_screen
from app.services.notify import ensure_user_thread
from app.states import UserState

router = Router(name="support")
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "support:open")
async def open_support(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    user.current_order_id = None
    await repo.set_state(session, user, UserState.DIALOG)
    await show_screen(bot, session, user, None, texts.SUPPORT, inline.in_dialog())
    # Create the client's shared support topic right away so the new dialog is
    # visible in the support chat immediately (and any misconfiguration shows
    # up in the logs instead of silently producing no topics).
    try:
        await ensure_user_thread(bot, session, user)
    except Exception:
        logger.exception("Could not ensure support thread for user %s", user.id)
    await call.answer()
