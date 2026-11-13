"""support_inbox -> support threads (the site's worker; replaced the removed support bot importer)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Staff,
    SupportAttachment,
    SupportConversation,
    SupportInbox,
    SupportThread,
    SupportThreadMessage,
    User,
)
from app.support import assign
from app.support.common import ASSIGNABLE_ROLES, now

logger = logging.getLogger(__name__)
SYSTEM_KINDS = {"delivery_failed", "close"}


@dataclass
class Notification:
    thread_id: int
    recipients: list[int]
    client_name: str
    cover_id: int | None
    text: str
    kind: str = "message"
    extra: dict = field(default_factory=dict)


async def process_inbox_batch(session: AsyncSession, limit: int = 50) -> list[Notification]:
    """Handle unprocessed inbox events, oldest first. The caller commits and then sends the notifications."""
    events = list(
        await session.scalars(
            select(SupportInbox)
            .where(SupportInbox.processed_at.is_(None))
            .order_by(SupportInbox.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    notifications: list[Notification] = []
    for event in events:
        note = await _process_event(session, event)
        if note is not None:
            notifications.append(note)
        event.processed_at = now()
    return notifications


async def _process_event(session: AsyncSession, event: SupportInbox) -> Notification | None:
    if await session.scalar(
        select(SupportThreadMessage.id).where(SupportThreadMessage.inbox_id == event.id)
    ):
        return None  # already imported
    conversation = await session.get(SupportConversation, event.conversation_id)
    user = await session.get(User, conversation.user_id) if conversation else None
    if conversation is None or user is None:
        logger.warning("Inbox event %s has no conversation/client", event.id)
        return None

    thread = await session.scalar(select(SupportThread).where(SupportThread.user_id == user.id).with_for_update())
    created = thread is None
    if thread is None:
        thread = SupportThread(
            user_id=user.id, channel=conversation.channel, status="open", unread=0,
            created_at=now(),
        )
        session.add(thread)
        await session.flush()
    thread.channel = conversation.channel
    if event.cover_id is not None:
        thread.last_cover_id = event.cover_id
    if event.avito_chat_ref is not None:
        thread.last_avito_chat_ref = event.avito_chat_ref

    is_system = event.kind in SYSTEM_KINDS
    message = SupportThreadMessage(
        thread_id=thread.id,
        direction="system" if is_system else "client",
        kind=event.kind,
        body=event.body,
        cover_id=event.cover_id,
        order_id=event.order_id,
        inbox_id=event.id,
        created_at=event.created_at or now(),
    )
    session.add(message)
    await session.flush()
    session.info["support_changed"] = True
    attached = (
        await session.execute(
            update(SupportAttachment)
            .where(SupportAttachment.inbox_id == event.id)
            .values(thread_message_id=message.id)
        )
    ).rowcount or 0
    thread.last_message_at = event.created_at or now()

    if event.kind == "close":
        thread.status = "closed"
        thread.needs_reply = False
        thread.waiting_since = None
        thread.next_step = None
        thread.due_at = None
    else:
        thread.status = "open"
        if not is_system:
            if not thread.needs_reply:
                thread.waiting_since = event.created_at or now()
            thread.needs_reply = True
            thread.last_client_message_at = event.created_at or now()
            thread.unread = (thread.unread or 0) + 1

    if not is_system and thread.assigned_to is not None:
        owner = await session.get(Staff, thread.assigned_to)
        if owner is None or not owner.is_active or not owner.on_shift:
            thread.assigned_to = None
            thread.assigned_at = None
    if created or (not is_system and thread.assigned_to is None):
        await assign.auto_assign(session, thread)
    await session.flush()

    if event.kind == "close":
        return None
    recipients: list[int]
    if thread.assigned_to is not None:
        recipients = [thread.assigned_to]
    else:
        recipients = list(
            await session.scalars(
                select(Staff.telegram_id).where(
                    Staff.role.in_(ASSIGNABLE_ROLES), Staff.is_active.is_(True), Staff.on_shift.is_(True)
                )
            )
        )
    return Notification(
        thread_id=thread.id,
        recipients=recipients,
        client_name=user.full_name or str(user.id),
        cover_id=event.cover_id,
        text=f"{event.body} 📎" if attached else event.body,
        kind=event.kind,
    )
