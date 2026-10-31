from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from support_bot.handlers.admin import process_add_staff
from support_bot.handlers.client import process_chat_message, process_new_request
from support_bot.models import BotUser
from support_bot.states import UserState

router = Router(name="text")


@router.message(F.chat.type == "private")
async def route_text(message: Message, bot: Bot, session: AsyncSession, user: BotUser) -> None:
    body = message.text or message.caption or ""
    if user.state == UserState.ADMIN_ADD_STAFF.value:
        await process_add_staff(bot, message, session, user)
        return
    if user.state == UserState.AWAIT_REQUEST.value:
        await process_new_request(bot, session, user, body)
        return
    if user.state == UserState.CHAT.value:
        await process_chat_message(bot, session, user, body)
        return
    if not body:
        await message.answer("Пока поддерживаются только текстовые сообщения.")
        return
    await message.answer("Выберите действие через /start или /requests.")
