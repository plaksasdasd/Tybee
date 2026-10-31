from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.db.models import User
from app.handlers.admin_panel import process_new_box, process_new_product
from app.handlers.article import process_article
from app.handlers.broadcast import process_broadcast
from app.handlers.personal import process_personal
from app.keyboards import inline
from app.services.screens import show_menu
from app.services.support_transport import queue_support_message
from app.states import UserState

router = Router(name="text")


@router.message(F.chat.type == "private")
async def route_text(message: Message, bot: Bot, session: AsyncSession, user: User) -> None:
    state = user.state

    if state == UserState.AWAIT_ARTICLE.value:
        await process_article(bot, session, user, message.text or "")
        return

    if state == UserState.ADMIN_ADD_PRODUCT.value:
        await process_new_product(message, session, user)
        return

    if state == UserState.ADMIN_ADD_BOX.value:
        await process_new_box(message, session, user)
        return

    if state == UserState.ADMIN_BROADCAST.value:
        await process_broadcast(message, bot, session, user)
        return

    if state == UserState.ADMIN_IMPORT_PRODUCTS.value:
        # В режиме импорта ждём документ; текст — напоминание
        # Не мешаем обработке документа (она в admin_panel), просто подсказываем
        if message.document is None:
            await message.answer(texts.ADMIN_IMPORT_PROMPT, reply_markup=inline.admin_import_cancel())
        return

    if state == UserState.AWAIT_PERSONAL.value:
        description = message.text or message.caption or "(без описания)"
        await process_personal(bot, session, user, description)
        return

    if state == UserState.DIALOG.value:
        body = message.text or message.caption or "[вложение]"
        await queue_support_message(
            session,
            user,
            body,
            order_id=user.current_order_id,
            external_id=f"telegram:{user.id}:{message.message_id}",
        )
        return

    # Default: gently bring the user back to the main menu (fresh, at the bottom).
    await show_menu(bot, session, user, force_new=True)
