from __future__ import annotations

import asyncio
import html
import logging
import re

from aiogram import Bot
from sqlalchemy import func, select

from app import texts
from app.db.base import session_factory
from app.db.models import Order, SupportOutbox, User
from app.keyboards import inline
from app.services.avito import get_avito
from app.services.notify import requisites
from app.services.robokassa import payment_link

logger = logging.getLogger(__name__)
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def _plain(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text))


async def _deliver(bot: Bot, event: SupportOutbox, user: User, order: Order | None) -> None:
    if user.source == "avito":
        if not user.avito_chat_id:
            raise RuntimeError(f"Avito chat is missing for user {user.id}")
        if event.kind == "invoice" and order is not None:
            description = f"{order.description}\n" if order.description else ""
            body = texts.AVITO_INVOICE.format(
                title=order.title or "Заказ",
                description=description,
                price=order.price or 0,
                requisites=_plain(requisites()),
            )
            if url := payment_link(order):
                body += f"\n\nСсылка на оплату: {url}"
        else:
            body = _plain(event.body)
        await get_avito().send_message(user.avito_chat_id, body)
        return

    if event.kind == "invoice" and order is not None:
        description = f"{html.escape(order.description)}\n" if order.description else ""
        body = texts.ORDER_CREATED.format(
            title=html.escape(order.title or ""),
            description=description,
            price=order.price,
            requisites=requisites(),
        )
        await bot.send_message(
            user.id,
            body,
            parse_mode="HTML",
            reply_markup=inline.payment_actions(order.id, payment_link(order)),
        )
        return
    if event.kind == "approval" and order is not None:
        await bot.send_message(
            user.id,
            texts.APPROVED.format(order_id=order.id),
            parse_mode="HTML",
            reply_markup=inline.open_dialog(order.id),
        )
        return
    await bot.send_message(user.id, event.body, parse_mode="HTML")


async def process_next_outbox(bot: Bot) -> bool:
    async with session_factory() as session:
        event = await session.scalar(
            select(SupportOutbox)
            .where(SupportOutbox.processed_at.is_(None))
            .order_by(SupportOutbox.id)
            .limit(1)
        )
        if event is None:
            return False
        user = await session.get(User, event.user_id)
        order = await session.get(Order, event.order_id) if event.order_id is not None else None
        try:
            if user is None:
                raise RuntimeError(f"User {event.user_id} not found")
            await _deliver(bot, event, user, order)
        except Exception as exc:
            event.attempts += 1
            event.last_error = str(exc)[:4000]
            await session.commit()
            logger.warning("Could not deliver outbox event %s: %s", event.id, exc)
            return False
        event.processed_at = func.now()
        event.last_error = None
        await session.commit()
        return True


async def outbox_worker(bot: Bot) -> None:
    while True:
        try:
            processed = await process_next_outbox(bot)
        except Exception:
            logger.exception("Outbox worker iteration failed")
            processed = False
        if not processed:
            await asyncio.sleep(1)
