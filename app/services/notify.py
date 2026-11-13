"""Support-queue events and routing of replies to the right cover."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Cover, Order, SupportInbox, User, UserCover
from app.services.support_transport import get_open_conversation, queue_support_message
from app.states import UserState


async def notify_new_personal(
    session: AsyncSession, user: User, order: Order, cover: Cover | None = None
) -> None:
    body = f"Персональная заявка по заказу #{order.id}\n\n{order.description or ''}"
    await queue_support_message(
        session,
        user,
        body,
        kind="personal",
        order_id=order.id,
        external_id=f"personal:{order.id}",
        cover=cover,
    )


async def resolve_reply_cover(
    session: AsyncSession, user_id: int, reply_to_cover_id: int | None = None
) -> int | None:
    """Which cover (bot) a message to this client must go through.

    1. The cover explicitly tied to the message being answered, if any.
    2. The cover where the client is in a support dialog right now
       (``user_covers.state == dialog``, most recently updated).
    3. The cover of the client's most recently updated order.
    4. The cover the client was last seen in.
    """
    if reply_to_cover_id is not None:
        return reply_to_cover_id

    in_dialog = await session.scalar(
        select(UserCover.cover_id)
        .where(UserCover.user_id == user_id, UserCover.state == UserState.DIALOG.value)
        .order_by(UserCover.updated_at.desc())
        .limit(1)
    )
    if in_dialog is not None:
        return in_dialog

    from_order = await session.scalar(
        select(Order.cover_id)
        .where(Order.user_id == user_id, Order.cover_id.is_not(None))
        .order_by(Order.updated_at.desc(), Order.id.desc())
        .limit(1)
    )
    if from_order is not None:
        return from_order

    return await session.scalar(
        select(UserCover.cover_id)
        .where(UserCover.user_id == user_id)
        .order_by(UserCover.last_seen_at.desc())
        .limit(1)
    )


async def resolve_dialog_cover(
    session: AsyncSession,
    user_id: int,
    *,
    explicit_cover_id: int | None = None,
    thread_cover_id: int | None = None,
) -> int | None:
    """Which bot can open a conversation with this client (staff-initiated dialog).

    1. The cover explicitly requested, but only if the client is actually there.
    2. The cover of the thread's last message, if it is still alive.
    3. The cover the client was last seen in.

    Only enabled, non-archived covers count: a message through a retired bot
    would never reach the client.
    """
    for candidate in (explicit_cover_id, thread_cover_id):
        if candidate is None:
            continue
        alive = await session.scalar(
            select(Cover.id)
            .join(UserCover, UserCover.cover_id == Cover.id)
            .where(
                Cover.id == candidate,
                Cover.enabled.is_(True),
                Cover.archived.is_(False),
                UserCover.user_id == user_id,
            )
        )
        if alive is not None:
            return int(alive)
    return await session.scalar(
        select(UserCover.cover_id)
        .join(Cover, Cover.id == UserCover.cover_id)
        .where(
            UserCover.user_id == user_id,
            Cover.enabled.is_(True),
            Cover.archived.is_(False),
        )
        .order_by(UserCover.last_seen_at.desc())
        .limit(1)
    )


async def notify_undeliverable(
    session: AsyncSession, user: User, reason: str, *, outbox_id: int
) -> None:
    """Tell the operator (in the client's support request) that a message to
    the client was NOT delivered — never silently switch to another cover."""
    conversation = await get_open_conversation(session, user)
    if conversation is None:
        return
    external_id = f"undelivered:{outbox_id}"
    if await session.scalar(
        select(SupportInbox.id).where(SupportInbox.external_id == external_id)
    ):
        return
    session.add(
        SupportInbox(
            conversation_id=conversation.id,
            kind="delivery_failed",
            body=f"не доставлено: {reason}",
            external_id=external_id,
        )
    )
    await session.flush()
