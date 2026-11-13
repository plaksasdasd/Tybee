from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.db.models import Cover, User, UserCover
from app.handlers.article import process_article
from app.handlers.personal import process_personal
from app.services.client_media import collect_telegram_media
from app.services.screens import show_menu
from app.services.support_transport import get_open_conversation, queue_support_message
from app.support.attachments import discard
from app.states import UserState

router = Router(name="text")


@router.message(F.chat.type == "private")
async def route_text(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    user: User,
    uc: UserCover,
    cover: Cover,
) -> None:
    state = uc.state

    if state == UserState.AWAIT_ARTICLE.value:
        await process_article(bot, session, user, uc, cover, message.text or "")
        return

    if state == UserState.AWAIT_PERSONAL.value:
        description = message.text or message.caption or "(без описания)"
        await process_personal(bot, session, user, uc, cover, description)
        return

    if state == UserState.MENU.value and await get_open_conversation(session, user) is not None:
        # Also handle conversations opened before the reply changed the local state,
        # or clients who viewed the menu while still talking to support.
        await repo.set_state(session, uc, UserState.DIALOG)
        state = uc.state

    if state == UserState.DIALOG.value:
        # The next bot screen must appear after the support conversation.
        uc.screen_message_id = None
        body, files = await collect_telegram_media(bot, message)
        event = await queue_support_message(
            session,
            user,
            body,
            order_id=uc.current_order_id,
            external_id=f"telegram:{cover.id}:{user.id}:{message.message_id}",
            cover=cover,
            attachments=files,
        )
        if event is None:
            discard(files)  # duplicate update: the files are already stored
        return

    # Default: gently bring the user back to the main menu (fresh, at the bottom).
    await show_menu(bot, session, user, uc, force_new=True)
