from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.handlers.article import process_article
from app.handlers.personal import process_personal
from app.services.notify import relay_user_to_support
from app.services.screens import show_menu
from app.states import UserState

router = Router(name="text")


@router.message(F.chat.type == "private")
async def route_text(message: Message, bot: Bot, session: AsyncSession, user: User) -> None:
    state = user.state

    if state == UserState.AWAIT_ARTICLE.value:
        await process_article(bot, session, user, message.text or "")
        return

    if state == UserState.AWAIT_PERSONAL.value:
        description = message.text or message.caption or "(без описания)"
        await process_personal(bot, session, user, description)
        return

    if state == UserState.DIALOG.value:
        delivered = await relay_user_to_support(bot, session, user, message)
        if not delivered:
            await message.answer(
                "⚠️ Команда временно недоступна. Попробуйте позже.", parse_mode="HTML"
            )
        return

    # Default: gently bring the user back to the main menu (fresh, at the bottom).
    await show_menu(bot, session, user, force_new=True)
