from __future__ import annotations

from aiogram.types import User as TgUser
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from support_bot.config import get_settings
from support_bot.db import session_factory
from support_bot.models import (
    BotUser,
    RequestNotification,
    Staff,
    SupportMessage,
    SupportRequest,
)
from support_bot.states import RequestStatus, UserState


async def get_or_create_user(session: AsyncSession, tg_user: TgUser) -> BotUser:
    user = await session.get(BotUser, tg_user.id)
    full_name = tg_user.full_name or ""
    if user is None:
        user = BotUser(id=tg_user.id, username=tg_user.username, full_name=full_name)
        session.add(user)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            user = await session.get(BotUser, tg_user.id)
            if user is None:
                raise
    user.username = tg_user.username
    user.full_name = full_name

    staff = await session.get(Staff, tg_user.id)
    if staff is not None and staff.display_name == f"Сотрудник #{tg_user.id}":
        staff.display_name = (full_name or tg_user.username or staff.display_name)[:80]
    return user


def is_admin(telegram_id: int) -> bool:
    return telegram_id in get_settings().admin_ids


async def get_staff(session: AsyncSession, telegram_id: int) -> Staff | None:
    return await session.get(Staff, telegram_id)


async def get_active_staff_member(session: AsyncSession, user: BotUser) -> Staff | None:
    staff = await get_staff(session, user.id)
    if staff is not None and staff.is_active:
        return staff
    if is_admin(user.id):
        return Staff(
            telegram_id=user.id,
            display_name=(user.full_name or user.username or f"Администратор #{user.id}")[:80],
            is_active=True,
        )
    return None


async def is_staff(session: AsyncSession, telegram_id: int) -> bool:
    if is_admin(telegram_id):
        return True
    staff = await get_staff(session, telegram_id)
    return staff is not None and staff.is_active


async def list_staff(session: AsyncSession) -> list[Staff]:
    rows = await session.scalars(
        select(Staff).order_by(Staff.is_active.desc(), Staff.display_name)
    )
    return list(rows)


async def save_staff(
    session: AsyncSession,
    telegram_id: int,
    display_name: str,
    *,
    is_active: bool | None = None,
) -> Staff:
    staff = await get_staff(session, telegram_id)
    if staff is None:
        staff = Staff(
            telegram_id=telegram_id,
            display_name=display_name,
            is_active=True if is_active is None else is_active,
        )
        session.add(staff)
    else:
        staff.display_name = display_name
        if is_active is not None:
            staff.is_active = is_active
    await session.flush()
    return staff


async def list_recipient_ids(session: AsyncSession) -> list[int]:
    rows = await session.scalars(select(Staff.telegram_id).where(Staff.is_active.is_(True)))
    return list(dict.fromkeys([*get_settings().admin_ids, *rows.all()]))


async def seed_staff() -> None:
    async with session_factory() as session:
        for telegram_id in dict.fromkeys([*get_settings().admin_ids, *get_settings().staff_ids]):
            if await get_staff(session, telegram_id) is None:
                session.add(Staff(telegram_id=telegram_id, display_name=f"Сотрудник #{telegram_id}"))
        await session.commit()


async def create_request(session: AsyncSession, user: BotUser, body: str) -> SupportRequest:
    request = SupportRequest(
        customer_id=user.id,
        customer_name=user.full_name,
        customer_username=user.username,
    )
    session.add(request)
    await session.flush()
    session.add(
        SupportMessage(request_id=request.id, sender_id=user.id, sender_kind="client", body=body)
    )
    user.active_request_id = request.id
    user.state = UserState.CHAT.value
    await session.flush()
    return request


async def get_request(session: AsyncSession, request_id: int) -> SupportRequest | None:
    return await session.get(SupportRequest, request_id)


async def add_message(
    session: AsyncSession,
    request: SupportRequest,
    sender_id: int,
    sender_kind: str,
    body: str,
) -> SupportMessage:
    message = SupportMessage(
        request_id=request.id, sender_id=sender_id, sender_kind=sender_kind, body=body
    )
    session.add(message)
    await session.flush()
    return message


async def list_messages(session: AsyncSession, request_id: int, limit: int = 50) -> list[SupportMessage]:
    rows = await session.scalars(
        select(SupportMessage)
        .where(SupportMessage.request_id == request_id)
        .order_by(SupportMessage.id.desc())
        .limit(limit)
    )
    return list(reversed(rows.all()))


async def list_customer_requests(session: AsyncSession, customer_id: int) -> list[SupportRequest]:
    rows = await session.scalars(
        select(SupportRequest)
        .where(SupportRequest.customer_id == customer_id)
        .order_by(SupportRequest.created_at.desc())
        .limit(20)
    )
    return list(rows)


async def list_staff_requests(
    session: AsyncSession, telegram_id: int, *, admin: bool
) -> list[SupportRequest]:
    condition = SupportRequest.status != RequestStatus.CLOSED.value
    if not admin:
        condition = or_(
            SupportRequest.status == RequestStatus.NEW.value,
            (SupportRequest.assigned_to == telegram_id)
            & (SupportRequest.status == RequestStatus.IN_PROGRESS.value),
        )
    rows = await session.scalars(
        select(SupportRequest)
        .where(condition)
        .order_by(SupportRequest.created_at.desc())
        .limit(30)
    )
    return list(rows)


async def claim_request(session: AsyncSession, request_id: int, staff: Staff) -> SupportRequest | None:
    result = await session.execute(
        update(SupportRequest)
        .where(
            SupportRequest.id == request_id,
            SupportRequest.status == RequestStatus.NEW.value,
        )
        .values(
            status=RequestStatus.IN_PROGRESS.value,
            assigned_to=staff.telegram_id,
            assigned_name=staff.display_name,
            claimed_at=func.now(),
            updated_at=func.now(),
        )
        .returning(SupportRequest)
    )
    return result.scalar_one_or_none()


async def close_request(session: AsyncSession, request: SupportRequest) -> None:
    request.status = RequestStatus.CLOSED.value
    request.closed_at = func.now()
    request.updated_at = func.now()
    for telegram_id in {request.customer_id, request.assigned_to}:
        if telegram_id is None:
            continue
        user = await session.get(BotUser, telegram_id)
        if user is not None and user.active_request_id == request.id:
            user.active_request_id = None
            user.state = UserState.MENU.value
    await session.flush()


async def remember_notification(
    session: AsyncSession, request_id: int, chat_id: int, message_id: int
) -> None:
    session.add(
        RequestNotification(request_id=request_id, chat_id=chat_id, message_id=message_id)
    )
    await session.flush()


async def list_notifications(session: AsyncSession, request_id: int) -> list[RequestNotification]:
    rows = await session.scalars(
        select(RequestNotification).where(RequestNotification.request_id == request_id)
    )
    return list(rows)
