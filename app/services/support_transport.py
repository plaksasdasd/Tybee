from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import AVITO_ID_BASE
from app.db.models import Cover, SupportAttachment, SupportConversation, SupportInbox, User
from app.support.attachments import StoredFile


async def get_or_create_avito_user(
    session: AsyncSession, avito_user_id: int, chat_id: str, name: str = ""
) -> User:
    """The client is the Avito author (one person across all profiles and chats).

    ``users.avito_chat_id`` is kept as the last chat for compatibility; routing uses ``avito_chats``."""
    user = await session.get(User, AVITO_ID_BASE + avito_user_id)
    if user is None:
        user = await session.scalar(
            select(User).where(User.source == "avito", User.avito_user_id == avito_user_id).limit(1)
        )
    if user is None:
        user = await session.scalar(select(User).where(User.avito_chat_id == chat_id))
    if user is not None:
        user.avito_chat_id = chat_id
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
    cover: Cover | None = None,
    attachments: Sequence[StoredFile] | None = None,
    avito_chat_ref: int | None = None,
) -> SupportInbox | None:
    """Queue an event for the support chat.

    ``cover`` is the cover the client wrote from: its id is stored on the event
    so staff see which bot the client is using. The body keeps only the client's
    words — the panel shows the cover as a separate service label.
    ``attachments`` are files already saved to disk; their rows are added in
    the same transaction as the event. Returns None for a duplicate
    ``external_id`` (the caller then discards the files).
    """
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
        cover_id=cover.id if cover is not None else None,
        avito_chat_ref=avito_chat_ref,
    )
    session.add(event)
    conversation.updated_at = func.now()
    await session.flush()
    for f in attachments or ():
        session.add(
            SupportAttachment(
                inbox_id=event.id, direction="in", ephemeral=True, kind=f.kind, mime=f.mime,
                original_name=f.original_name, size=f.size, sha256=f.sha256, path=f.path,
                remote_provider=f.remote_provider, remote_id=f.remote_id,
                remote_cover_id=f.remote_cover_id or (cover.id if f.remote_provider == "telegram" and cover else None),
                remote_profile_id=f.remote_profile_id,
            )
        )
    if attachments:
        await session.flush()
    return event


async def close_support_dialog(
    session: AsyncSession,
    user: User,
    *,
    order_id: int | None = None,
    external_id: str | None = None,
) -> None:
    conversation = await get_open_conversation(session, user)
    if conversation is None:
        return
    conversation.status = "closed"
    session.add(
        SupportInbox(
            conversation_id=conversation.id,
            order_id=order_id,
            kind="close",
            body="Клиент завершил диалог",
            external_id=external_id,
        )
    )
    await session.flush()


