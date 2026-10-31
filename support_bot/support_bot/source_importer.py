from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from support_bot import repositories as repo
from support_bot.db import session_factory
from support_bot.keyboards import payment_actions
from support_bot.models import SupportMessage, SupportRequest
from support_bot.services import (
    esc,
    notify_new_request,
    safe_body_message,
    sync_claim_notifications,
)
from support_bot.source_db import source_enabled, source_session_factory
from support_bot.source_models import SourceConversation, SourceInbox, SourceUser
from support_bot.states import RequestStatus

logger = logging.getLogger(__name__)


async def _notify_payment(
    bot: Bot, session: AsyncSession, request: SupportRequest, event: SourceInbox
) -> None:
    for staff_id in await repo.list_recipient_ids(session):
        try:
            await bot.send_message(
                staff_id,
                f"💰 <b>Обращение #{request.id}</b>\n{esc(event.body)}",
                parse_mode="HTML",
                reply_markup=payment_actions(event.order_id),
            )
        except TelegramAPIError as exc:
            logger.warning("Could not send payment panel to %s: %s", staff_id, exc)


async def process_next_source_event(bot: Bot) -> bool:
    if not source_enabled():
        return False
    async with source_session_factory() as source, session_factory() as session:
        event = await source.scalar(
            select(SourceInbox)
            .where(SourceInbox.processed_at.is_(None))
            .order_by(SourceInbox.id)
            .limit(1)
        )
        if event is None:
            return False
        duplicate = await session.scalar(
            select(SupportMessage).where(SupportMessage.source_event_id == event.id)
        )
        if duplicate is not None:
            event.processed_at = func.now()
            await source.commit()
            return True

        conversation = await source.get(SourceConversation, event.conversation_id)
        customer = await source.get(SourceUser, conversation.user_id) if conversation else None
        if conversation is None or customer is None:
            event.processed_at = func.now()
            await source.commit()
            logger.warning("Source event %s has no conversation/customer", event.id)
            return True

        request = await session.scalar(
            select(SupportRequest).where(
                SupportRequest.source_conversation_id == conversation.id
            )
        )
        created = request is None
        if request is None:
            request = SupportRequest(
                customer_id=customer.id,
                customer_name=customer.full_name,
                customer_username=customer.username,
                source="main",
                channel=conversation.channel,
                source_conversation_id=conversation.id,
            )
            session.add(request)
            await session.flush()
        if event.order_id is not None:
            request.source_order_id = event.order_id

        if event.kind == "close":
            request.status = RequestStatus.CLOSED.value
            request.closed_at = func.now()
            session.add(
                SupportMessage(
                    request_id=request.id,
                    sender_id=customer.id,
                    sender_kind="system",
                    body=event.body,
                    source_event_id=event.id,
                )
            )
            await sync_claim_notifications(bot, session, request)
        else:
            session.add(
                SupportMessage(
                    request_id=request.id,
                    sender_id=customer.id,
                    sender_kind="client",
                    body=event.body,
                    source_event_id=event.id,
                )
            )
            await session.flush()
            if created:
                await notify_new_request(bot, session, request, event.body)
            elif request.assigned_to is not None:
                await safe_body_message(
                    bot, request.assigned_to, request.id, "Клиент", event.body
                )
            if event.kind == "payment_review" and event.order_id is not None:
                await _notify_payment(bot, session, request, event)

        event.processed_at = func.now()
        await session.commit()
        await source.commit()
        return True


async def source_importer_worker(bot: Bot) -> None:
    while True:
        try:
            processed = await process_next_source_event(bot)
        except Exception:
            logger.exception("Source importer iteration failed")
            processed = False
        if not processed:
            await asyncio.sleep(1)
