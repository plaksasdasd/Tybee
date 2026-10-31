from __future__ import annotations

import html
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from support_bot import repositories as repo
from support_bot.keyboards import claim_request
from support_bot.models import SupportMessage, SupportRequest
from support_bot.states import RequestStatus

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    RequestStatus.NEW.value: "Новое",
    RequestStatus.IN_PROGRESS.value: "В работе",
    RequestStatus.CLOSED.value: "Закрыто",
}


def esc(value: object | None) -> str:
    return html.escape(str(value or ""))


def esc_limited(value: str, limit: int) -> str:
    escaped = esc(value)
    if len(escaped) <= limit:
        return escaped
    result = ""
    for char in value:
        encoded = html.escape(char)
        if len(result) + len(encoded) >= limit:
            return f"{result}…"
        result += encoded
    return result


def html_chunks(value: str, limit: int = 2400) -> list[str]:
    chunks: list[str] = []
    current = ""
    for char in value:
        encoded = html.escape(char)
        if current and len(current) + len(encoded) > limit:
            chunks.append(current)
            current = ""
        current += encoded
    if current:
        chunks.append(current)
    return chunks


def customer_label(request: SupportRequest) -> str:
    name = esc(request.customer_name) or f"ID {request.customer_id}"
    if request.customer_username:
        name += f" (@{esc(request.customer_username)})"
    return name


def request_notification(request: SupportRequest, body: str) -> str:
    text = (
        f"🆕 <b>Обращение #{request.id}</b>\n"
        f"Клиент: {customer_label(request)}\n\n"
        f"{esc_limited(body, 2400)}"
    )
    if request.status == RequestStatus.CLOSED.value:
        text += "\n\n🔒 <b>Обращение закрыто</b>"
    elif request.assigned_to is not None:
        text += f"\n\n✅ <b>В работе у {esc(request.assigned_name)}</b>"
    return text


def request_details(request: SupportRequest, messages: list[SupportMessage]) -> str:
    lines = [
        f"📋 <b>Обращение #{request.id}</b>",
        f"Клиент: {customer_label(request)}",
        f"Статус: <b>{STATUS_LABELS.get(request.status, request.status)}</b>",
    ]
    if request.assigned_to is not None:
        lines.append(f"Сотрудник: <b>{esc(request.assigned_name)}</b>")
    lines.append("\n<b>История:</b>")
    for message in messages[-6:]:
        if message.sender_kind == "client":
            sender = "Клиент"
        elif message.sender_kind == "staff":
            sender = "Поддержка"
        else:
            sender = "Система"
        lines.append(f"\n<b>{sender}:</b>\n{esc_limited(message.body, 400)}")
    return "\n".join(lines)


async def safe_message(bot: Bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
    except TelegramAPIError as exc:
        logger.warning("Could not send a support message to %s: %s", chat_id, exc)


async def safe_body_message(
    bot: Bot, chat_id: int, request_id: int, sender: str, body: str
) -> None:
    for index, chunk in enumerate(html_chunks(body), start=1):
        continuation = f" (продолжение {index})" if index > 1 else ""
        await safe_message(
            bot,
            chat_id,
            f"💬 <b>Обращение #{request_id}{continuation}</b>\n<b>{sender}:</b>\n{chunk}",
        )


async def notify_new_request(
    bot: Bot, session: AsyncSession, request: SupportRequest, body: str
) -> None:
    for chat_id in await repo.list_recipient_ids(session):
        try:
            sent = await bot.send_message(
                chat_id,
                request_notification(request, body),
                parse_mode="HTML",
                reply_markup=claim_request(request.id),
            )
        except TelegramAPIError as exc:
            logger.warning("Could not notify staff %s: %s", chat_id, exc)
            continue
        await repo.remember_notification(session, request.id, chat_id, sent.message_id)


async def sync_claim_notifications(bot: Bot, session: AsyncSession, request: SupportRequest) -> None:
    first_message = await session.scalar(
        select(SupportMessage)
        .where(SupportMessage.request_id == request.id)
        .order_by(SupportMessage.id)
        .limit(1)
    )
    body = first_message.body if first_message is not None else ""
    for notification in await repo.list_notifications(session, request.id):
        try:
            await bot.edit_message_text(
                request_notification(request, body),
                chat_id=notification.chat_id,
                message_id=notification.message_id,
                parse_mode="HTML",
                reply_markup=None,
            )
        except TelegramAPIError as exc:
            logger.warning("Could not update request notification %s: %s", notification.id, exc)
