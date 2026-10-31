from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import AVITO_ID_BASE
from app.db.models import SupportConversation, SupportInbox, User


async def get_or_create_avito_user(
    session: AsyncSession, avito_user_id: int, chat_id: str, name: str = ""
) -> User:
    user = await session.scalar(select(User).where(User.avito_chat_id == chat_id))
    if user is not None:
        return user
    user = User(
        id=AVITO_ID_BASE + avito_user_id,
        full_name=name or f"Avito {avito_user_id}",
        source="avito",
        avito_user_id=avito_user_id,
        avito_chat_id=chat_id,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise
    return user


async def get_open_conversation(session: AsyncSession, user: User) -> SupportConversation | None:
    return await session.scalar(
        select(SupportConversation)
        .where(
            SupportConversation.user_id == user.id,
            SupportConversation.status == "open",
        )
        .order_by(SupportConversation.id.desc())
        .limit(1)
    )


async def queue_support_message(
    session: AsyncSession,
    user: User,
    body: str,
    *,
    kind: str = "message",
    order_id: int | None = None,
    external_id: str | None = None,
) -> SupportInbox | None:
    if external_id is not None:
        existing = await session.scalar(
            select(SupportInbox).where(SupportInbox.external_id == external_id)
        )
        if existing is not None:
            return None
    conversation = await get_open_conversation(session, user)
    if conversation is None:
        conversation = SupportConversation(user_id=user.id, channel=user.source)
        session.add(conversation)
        await session.flush()
    event = SupportInbox(
        conversation_id=conversation.id,
        order_id=order_id,
        kind=kind,
        body=body,
        external_id=external_id,
    )
    session.add(event)
    conversation.updated_at = func.now()
    await session.flush()
    return event


async def close_support_dialog(
    session: AsyncSession, user: User, *, external_id: str | None = None
) -> None:
    conversation = await get_open_conversation(session, user)
    if conversation is None:
        return
    conversation.status = "closed"
    session.add(
        SupportInbox(
            conversation_id=conversation.id,
            order_id=user.current_order_id,
            kind="close",
            body="Клиент завершил диалог",
            external_id=external_id,
        )
    )
    await session.flush()


