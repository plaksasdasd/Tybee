from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from support_bot import repositories as repo
from support_bot.handlers.client import show_request
from support_bot.models import BotUser
from support_bot.services import esc, safe_message, sync_claim_notifications
from support_bot.source_repository import queue_source_outbox
from support_bot.states import RequestStatus, UserState

router = Router(name="staff")


@router.callback_query(F.data.startswith("request:claim:"))
async def claim(call: CallbackQuery, bot: Bot, session: AsyncSession, user: BotUser) -> None:
    staff = await repo.get_active_staff_member(session, user)
    if staff is None:
        await call.answer("Доступно только сотрудникам поддержки", show_alert=True)
        return
    request_id = int(call.data.split(":")[2])
    request = await repo.get_request(session, request_id)
    if request is None:
        await call.answer("Обращение не найдено", show_alert=True)
        return
    if request.status != RequestStatus.NEW.value:
        await call.answer("Обращение уже взято в работу", show_alert=True)
        return

    claimed = await repo.claim_request(session, request_id, staff)
    if claimed is None:
        await session.refresh(request)
        await sync_claim_notifications(bot, session, request)
        await call.answer("Обращение уже взял другой сотрудник", show_alert=True)
        return

    user.state = UserState.CHAT.value
    user.active_request_id = request_id
    await repo.add_message(
        session,
        claimed,
        user.id,
        "system",
        f"Обращение взял в работу сотрудник {staff.display_name}",
    )
    await sync_claim_notifications(bot, session, claimed)
    text = f"Сотрудник <b>{esc(staff.display_name)}</b> взял ваше обращение в работу."
    if claimed.source == "main":
        await queue_source_outbox(claimed, text)
    else:
        await safe_message(bot, claimed.customer_id, text)
    await show_request(bot, session, user, claimed)
    await call.answer("Обращение назначено вам")
