"""What staff do in a thread. Every action queues an outbox event and writes a system note; callers commit."""
from __future__ import annotations

import html
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AvitoChat,
    Order,
    Staff,
    SupportAttachment,
    SupportConversation,
    SupportOutbox,
    SupportThread,
    SupportThreadMessage,
    User,
)
from app.support.attachments import StoredFile
from app.support.common import (
    DELIVERY,
    NO_PING_STATUSES,
    ORDER_STATUS_LABELS,
    PAID,
    SETTABLE,
    UNPAID,
    SupportError,
    now,
    ts,
)

PING_INTERVAL = timedelta(hours=24)


def _note(thread: SupportThread, staff: Staff, kind: str, body: str, order_id: int | None = None):
    return SupportThreadMessage(
        thread_id=thread.id, direction="system", kind=kind, staff_id=staff.telegram_id, body=body,
        order_id=order_id,
    )


async def _ensure_open_conversation(session: AsyncSession, thread: SupportThread) -> None:
    """Delivery failures are reported through an open conversation, so keep one while staff talk."""
    existing = await session.scalar(
        select(SupportConversation.id).where(
            SupportConversation.user_id == thread.user_id, SupportConversation.status == "open"
        )
    )
    if existing is None:
        session.add(SupportConversation(user_id=thread.user_id, channel=thread.channel))


async def reply(
    session: AsyncSession, thread: SupportThread, staff: Staff, text: str, file: StoredFile | None = None
) -> SupportThreadMessage:
    """Queue a reply (text and/or one file). The caption/text is ``<b>{name}:</b>\n{text}``."""
    attachment = None
    if file is not None:
        attachment = SupportAttachment(
            direction="out", ephemeral=True, kind=file.kind, mime=file.mime, original_name=file.original_name,
            size=file.size, sha256=file.sha256, path=file.path,
        )
        session.add(attachment)
        await session.flush()
    name = f"<b>{html.escape(staff.display_name)}:</b>"
    if thread.channel == "telegram":
        name = f"💬 <b>Поддержка</b>\n{name}"
    outbox = SupportOutbox(
        user_id=thread.user_id,
        kind="text",
        body=f"{name}\n{html.escape(text)}" if text else name,
        cover_id=thread.last_cover_id,
        avito_chat_ref=thread.last_avito_chat_ref,
        attachment_id=attachment.id if attachment is not None else None,
    )
    session.add(outbox)
    await session.flush()
    msg = SupportThreadMessage(
        thread_id=thread.id, direction="staff", kind="message", staff_id=staff.telegram_id, body=text,
        cover_id=thread.last_cover_id, outbox_id=outbox.id,
    )
    session.add(msg)
    if attachment is not None:
        await session.flush()
        attachment.outbox_id = outbox.id
        attachment.thread_message_id = msg.id
    thread.status = "open"
    thread.last_message_at = now()
    await _ensure_open_conversation(session, thread)
    await session.flush()
    return msg


async def start_dialog(
    session: AsyncSession,
    user: User,
    staff: Staff,
    text: str,
    file: StoredFile | None = None,
    *,
    cover_id: int | None = None,
) -> tuple[SupportThread, SupportThreadMessage]:
    """Open (or reopen) the client's thread and send the first message.

    The ``initiate`` system note marks the dialog as started by staff, so payroll
    and the leaderboard do not count it as a "new client" bonus."""
    thread = await session.scalar(
        select(SupportThread).where(SupportThread.user_id == user.id).with_for_update()
    )
    if thread is None:
        thread = SupportThread(
            user_id=user.id, channel="telegram", status="open",
            assigned_to=staff.telegram_id, assigned_at=now(),
            last_cover_id=cover_id, unread=0, needs_reply=False,
        )
        session.add(thread)
        await session.flush()
    else:
        if thread.channel == "avito":
            raise SupportError("Клиент пишет через Avito: откройте его ветку", 409)
        thread.status = "open"
        thread.assigned_to = staff.telegram_id
        thread.assigned_at = now()
        # Staff are writing right now: the queue no longer waits for an answer.
        thread.needs_reply = False
        thread.waiting_since = None
        if cover_id is not None:
            thread.last_cover_id = cover_id
    session.add(_note(thread, staff, "initiate", f"Диалог начат сотрудником: {staff.display_name}"))
    msg = await reply(session, thread, staff, text, file)
    return thread, msg


async def create_invoice(
    session: AsyncSession, thread: SupportThread, staff: Staff, title: str, description: str, price: int
) -> Order:
    user = await session.get(User, thread.user_id)
    if user is None:
        raise SupportError("Клиент не найден", 404)
    while True:
        order_id = secrets.randbelow(1_900_000_000) + 100_000_000
        if await session.get(Order, order_id) is None:
            break
    avito_profile_id = None
    if user.source == "avito" and thread.last_avito_chat_ref is not None:
        avito_profile_id = await session.scalar(
            select(AvitoChat.profile_id).where(AvitoChat.id == thread.last_avito_chat_ref)
        )
    order = Order(
        id=order_id, user_id=user.id, type="personal", status="awaiting_payment",
        title=title, description=description or None, price=price, cover_id=thread.last_cover_id,
        avito_profile_id=avito_profile_id,
    )
    session.add(order)
    user.current_order_id = order.id
    await session.flush()
    session.add(
        SupportOutbox(
            user_id=user.id, order_id=order.id, kind="invoice", body=f"Счёт по заказу #{order.id}",
            cover_id=thread.last_cover_id, avito_chat_ref=thread.last_avito_chat_ref,
        )
    )
    session.add(_note(thread, staff, "invoice", f"Выставлен счёт по заказу #{order.id}: {title}, {price} ₽", order.id))
    thread.last_message_at = now()
    await session.flush()
    return order


def check_transition(current: str, new: str, *, responsible: bool, can_refund: bool) -> None:
    """ТЗ-2, B.3. Raises SupportError: 422 unknown status, 403 no right, 409 forbidden transition."""
    if new not in SETTABLE:
        raise SupportError("Неизвестный статус", 422)
    if new == current:
        raise SupportError("Заказ уже находится в этом статусе", 409)
    if new == "cancelled":
        if current in UNPAID:
            if not responsible:
                raise SupportError("Вы не ведёте ветку этого клиента", 403)
            return
        if current in PAID or current in ("refund_pending", "refunded"):
            if not can_refund:
                raise SupportError("Оплаченный заказ отменяет только агент арбитража после возврата", 403)
            if current != "refunded":
                raise SupportError("Недопустимый переход: сначала оформите возврат", 409)
            return
        raise SupportError("Недопустимый переход", 409)
    if new in ("refund_pending", "refunded"):
        if not can_refund:
            raise SupportError("Возвраты оформляет только агент арбитража", 403)
        allowed = PAID if new == "refund_pending" else ("refund_pending",)
        if current not in allowed:
            raise SupportError("Недопустимый переход", 409)
        return
    # delivery statuses
    if not responsible:
        raise SupportError("Вы не ведёте ветку этого клиента", 403)
    if current not in ("approved", *DELIVERY):
        raise SupportError("Недопустимый переход", 409)


async def change_order_status(
    session: AsyncSession,
    thread: SupportThread,
    staff: Staff,
    order_id: int,
    status: str,
    *,
    responsible: bool = True,
    can_refund: bool = False,
) -> Order:
    order = await session.get(Order, order_id)
    if order is None or order.user_id != thread.user_id:
        raise SupportError("Заказ не найден", 404)
    check_transition(order.status, status, responsible=responsible, can_refund=can_refund)
    order.status = status
    if status == "refunded":
        order.refunded_at = now()
        order.refund_amount = order.paid_amount or 0
    label = ORDER_STATUS_LABELS[status]
    session.add(
        SupportOutbox(
            user_id=order.user_id, order_id=order.id, kind=f"status:{status}",
            body=f"📦 Заказ #{order.id}: новый статус — <b>{label}</b>",
        )
    )
    note = f"Заказ #{order.id}: {label}"
    if status == "refunded":
        note += f" ({order.refund_amount} ₽)"
    session.add(_note(thread, staff, "status", note, order.id))
    thread.last_message_at = now()
    await session.flush()
    return order


async def ping_order(session: AsyncSession, thread: SupportThread, staff: Staff, order: Order) -> None:
    """ТЗ-2, B.5: remind the client about an order (at most once per 24 h per order)."""
    if order.user_id != thread.user_id:
        raise SupportError("Заказ не найден", 404)
    if order.status in NO_PING_STATUSES:
        raise SupportError("Для заказа в этом статусе напоминание не отправляется", 409)
    current = now()
    result = await session.execute(update(Order).where(
        Order.id == order.id, Order.user_id == thread.user_id,
        ~Order.status.in_(NO_PING_STATUSES),
        or_(Order.last_ping_at.is_(None), Order.last_ping_at <= current - PING_INTERVAL),
    ).values(last_ping_at=current, updated_at=Order.updated_at).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        await session.refresh(order)
        if order.status in NO_PING_STATUSES:
            raise SupportError("Для заказа в этом статусе напоминание не отправляется", 409)
        next_at = ts(order.last_ping_at) + PING_INTERVAL.total_seconds() if order.last_ping_at else current.timestamp() + PING_INTERVAL.total_seconds()
        if next_at > current.timestamp():
            when = datetime.fromtimestamp(next_at, timezone.utc)
            raise SupportError(
                f"Напоминание уже отправлялось. Следующее можно после {when:%d.%m.%Y %H:%M} UTC",
                429, retry_after=int(next_at - current.timestamp()) + 1,
            )
    # The conditional UPDATE above preserves updated_at and claims the cooldown atomically.
    await session.refresh(order)
    session.add(
        SupportOutbox(
            user_id=order.user_id, order_id=order.id, kind="ping",
            body=f"🔔 Напоминание по заказу #{order.id}", cover_id=order.cover_id,
            avito_chat_ref=thread.last_avito_chat_ref,
        )
    )
    session.add(_note(thread, staff, "ping", f"🔔 Напоминание по заказу #{order.id}", order.id))
    thread.last_message_at = current
    await _ensure_open_conversation(session, thread)
    await session.flush()


async def set_banned(session: AsyncSession, thread: SupportThread, staff: Staff, banned: bool) -> None:
    user = await session.get(User, thread.user_id)
    if user is None:
        raise SupportError("Клиент не найден", 404)
    user.is_banned = banned
    session.add(_note(thread, staff, "ban", "Клиент заблокирован" if banned else "Клиент разблокирован"))
    await session.flush()


async def close_thread(session: AsyncSession, thread: SupportThread, staff: Staff) -> None:
    thread.status = "closed"
    thread.unread = 0
    thread.needs_reply = False
    thread.waiting_since = None
    thread.next_step = None
    thread.due_at = None
    for conv in await session.scalars(
        select(SupportConversation).where(
            SupportConversation.user_id == thread.user_id, SupportConversation.status == "open"
        )
    ):
        conv.status = "closed"
    session.add(SupportOutbox(user_id=thread.user_id, kind="text", body="Диалог с поддержкой завершён.",
                              cover_id=thread.last_cover_id, avito_chat_ref=thread.last_avito_chat_ref))
    session.add(_note(thread, staff, "close", f"Диалог закрыт: {staff.display_name}"))
    await session.flush()
