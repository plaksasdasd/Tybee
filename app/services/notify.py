"""Admin notifications and the user<->team message relay."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.config import get_settings
from app.db.models import Order, User
from app.keyboards import inline

logger = logging.getLogger(__name__)


def _client(user: User) -> str:
    return repo.display_name(user) or f"id {user.id}"


async def _send_to_support(bot: Bot, session: AsyncSession, text: str, user: User, **kwargs) -> None:
    target = get_settings().support_target
    if target is None:
        logger.warning("No support target configured; dropping notification")
        return
    sent = await bot.send_message(target, text, parse_mode="HTML", **kwargs)
    await repo.remember_relay(session, sent.message_id, user.id)


async def notify_new_payment(bot: Bot, session: AsyncSession, user: User, order: Order) -> None:
    text = texts.ADMIN_NEW_PAYMENT.format(
        order_id=order.id,
        title=order.title,
        code=order.article,
        price=order.price,
        client=_client(user),
    )
    await _send_to_support(
        bot, session, text, user, reply_markup=inline.admin_payment(order.id)
    )


async def notify_new_personal(bot: Bot, session: AsyncSession, user: User, order: Order) -> None:
    text = texts.ADMIN_NEW_PERSONAL.format(
        order_id=order.id, client=_client(user), description=order.description or ""
    )
    await _send_to_support(bot, session, text, user)


async def relay_user_to_support(
    bot: Bot, session: AsyncSession, user: User, message: Message
) -> bool:
    target = get_settings().support_target
    if target is None:
        return False
    order_id = user.current_order_id or 0
    header = texts.ADMIN_DIALOG_MSG.format(client=_client(user), order_id=order_id)
    sent = await bot.send_message(target, header, parse_mode="HTML")
    await repo.remember_relay(session, sent.message_id, user.id)
    # Copy the actual content (keeps photos, documents, etc. intact).
    copied = await bot.copy_message(target, message.chat.id, message.message_id)
    await repo.remember_relay(session, copied.message_id, user.id)
    return True


async def relay_admin_to_user(bot: Bot, user_id: int, message: Message) -> None:
    if message.text:
        await bot.send_message(
            user_id, f"{texts.DIALOG_REPLY_PREFIX}{message.html_text}", parse_mode="HTML"
        )
    else:
        await bot.copy_message(user_id, message.chat.id, message.message_id)
