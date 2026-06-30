"""Admin notifications and the user<->team message relay.

When ``SUPPORT_CHAT_ID`` points at a forum supergroup, every client gets their
own **topic (thread)** there: all payments, orders and the two-way conversation
are posted into that thread. When the support chat is a plain chat (or a single
admin DM), we fall back to the legacy reply-based relay.
"""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
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


def requisites() -> str:
    """Payment requisites text — configurable via ``PAYMENT_REQUISITES``."""
    return get_settings().payment_requisites.strip() or texts.DEFAULT_REQUISITES


def status_label(status: str) -> str:
    return texts.STATUS_LABELS.get(status, status)


async def ensure_user_thread(bot: Bot, session: AsyncSession, user: User) -> int | None:
    """Return the client's support-chat thread id, creating the topic if needed.

    Returns ``None`` when the support chat is not a forum supergroup (topics are
    unsupported there) so callers fall back to the reply-based relay.
    """
    chat_id = get_settings().support_chat_id
    if chat_id is None:
        return None
    if user.support_thread_id is not None:
        return user.support_thread_id
    try:
        topic = await bot.create_forum_topic(chat_id, name=f"{_client(user)} · {user.id}")
    except TelegramBadRequest as exc:
        logger.info("Support chat is not a forum, no per-client thread: %s", exc)
        return None
    user.support_thread_id = topic.message_thread_id
    try:
        await bot.send_message(
            chat_id,
            texts.THREAD_INTRO.format(client=_client(user), user_id=user.id),
            parse_mode="HTML",
            message_thread_id=topic.message_thread_id,
        )
    except TelegramBadRequest as exc:
        logger.info("Could not post thread intro: %s", exc)
    return topic.message_thread_id


async def _support_destination(
    bot: Bot, session: AsyncSession, user: User
) -> tuple[int | None, int | None]:
    """Resolve (chat_id, thread_id) where this client's support messages go."""
    settings = get_settings()
    if settings.support_chat_id is not None:
        thread = await ensure_user_thread(bot, session, user)
        return settings.support_chat_id, thread
    admins = settings.admin_ids
    return (admins[0] if admins else None), None


async def _send_to_support(
    bot: Bot, session: AsyncSession, text: str, user: User, **kwargs
) -> Message | None:
    chat_id, thread_id = await _support_destination(bot, session, user)
    if chat_id is None:
        logger.warning("No support target configured; dropping notification")
        return None
    if thread_id is not None:
        kwargs.setdefault("message_thread_id", thread_id)
    sent = await bot.send_message(chat_id, text, parse_mode="HTML", **kwargs)
    await repo.remember_relay(session, sent.message_id, user.id)
    return sent


async def notify_new_payment(bot: Bot, session: AsyncSession, user: User, order: Order) -> None:
    text = texts.ADMIN_NEW_PAYMENT.format(
        order_id=order.id,
        title=order.title or "—",
        code=order.article or "—",
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


async def notify_new_order(bot: Bot, session: AsyncSession, user: User, order: Order) -> None:
    """Announce a support-created order in the client's thread."""
    text = texts.ADMIN_NEW_ORDER.format(
        order_id=order.id,
        title=order.title or "—",
        price=order.price,
        client=_client(user),
    )
    await _send_to_support(bot, session, text, user)


async def post_order_panel(bot: Bot, session: AsyncSession, user: User, order: Order) -> None:
    """Post the order's status control panel into the client's support thread."""
    text = texts.ORDER_PANEL.format(
        order_id=order.id,
        title=order.title or "Заказ",
        status=status_label(order.status),
    )
    await _send_to_support(
        bot, session, text, user, reply_markup=inline.order_status(order.id, order.status)
    )


async def notify_order_status(bot: Bot, user_id: int, order: Order) -> None:
    """Notify the client that their order status changed."""
    await bot.send_message(
        user_id,
        texts.ORDER_STATUS_NOTIFY.format(
            order_id=order.id, status=status_label(order.status)
        ),
        parse_mode="HTML",
    )


async def relay_user_to_support(
    bot: Bot, session: AsyncSession, user: User, message: Message
) -> bool:
    chat_id, thread_id = await _support_destination(bot, session, user)
    if chat_id is None:
        return False
    order_id = user.current_order_id or 0
    header = texts.ADMIN_DIALOG_MSG.format(client=_client(user), order_id=order_id)
    kwargs = {"message_thread_id": thread_id} if thread_id is not None else {}
    sent = await bot.send_message(chat_id, header, parse_mode="HTML", **kwargs)
    await repo.remember_relay(session, sent.message_id, user.id)
    # Copy the actual content (keeps photos, documents, etc. intact).
    copied = await bot.copy_message(chat_id, message.chat.id, message.message_id, **kwargs)
    await repo.remember_relay(session, copied.message_id, user.id)
    return True


async def relay_admin_to_user(bot: Bot, user_id: int, message: Message) -> None:
    if message.text:
        await bot.send_message(
            user_id, f"{texts.DIALOG_REPLY_PREFIX}{message.html_text}", parse_mode="HTML"
        )
    else:
        await bot.copy_message(user_id, message.chat.id, message.message_id)
