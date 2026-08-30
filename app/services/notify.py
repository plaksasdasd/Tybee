"""Admin notifications and the user<->team message relay.

When ``SUPPORT_CHAT_ID`` points at a forum supergroup, every client gets one
shared support topic. All orders and messages for that client are routed there.
When the support chat is a plain chat (or a single admin DM), messages use the
reply-based relay.

Clients coming from Avito (``User.source == "avito"``) have no Telegram chat —
everything addressed to them is delivered through the Avito Messenger API.
"""
from __future__ import annotations

import html as html_module
import logging
import re
from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.assets import Banner
from app.config import get_settings
from app.db.models import Order, User
from app.keyboards import inline
from app.services.avito import get_avito

logger = logging.getLogger(__name__)

# Telegram limits forum topic names to 128 characters.
_THREAD_NAME_MAX = 128

_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def _plain(text: str) -> str:
    """Convert Telegram HTML to plain text (Avito renders no formatting)."""
    return html_module.unescape(_TAG_RE.sub("", text))


def _esc(value: object | None) -> str:
    """Escape client-controlled text for embedding into HTML messages."""
    return html_module.escape(str(value or ""))


def requisites_plain() -> str:
    return _plain(requisites())


def _client(user: User) -> str:
    return repo.display_name(user) or f"id {user.id}"


def _thread_name(user: User) -> str:
    return f"{_client(user)} · {user.id}"[:_THREAD_NAME_MAX]


def requisites() -> str:
    """Payment requisites text — configurable via ``PAYMENT_REQUISITES``."""
    return get_settings().payment_requisites.strip() or texts.DEFAULT_REQUISITES


def status_label(status: str) -> str:
    return texts.STATUS_LABELS.get(status, status)


def _is_stale_thread(exc: TelegramAPIError) -> bool:
    """True when the stored thread id points at a deleted topic."""
    text = str(exc).lower()
    return "thread" in text and "not found" in text


async def ensure_user_thread(bot: Bot, session: AsyncSession, user: User) -> int | None:
    """Return the client's support-chat thread id, creating the topic if needed.

    Returns ``None`` when the support chat is not a forum supergroup (or the
    bot cannot create topics there) so callers fall back to the reply-based
    relay. Failures are logged loudly: a misconfigured chat — topics disabled,
    missing admin rights or a wrong ``SUPPORT_CHAT_ID`` — must be visible in
    the logs instead of silently producing no dialogs.
    """
    chat_id = get_settings().support_chat_id
    if chat_id is None:
        return None
    if user.support_thread_id is not None:
        return user.support_thread_id
    try:
        topic = await bot.create_forum_topic(chat_id, name=_thread_name(user))
    except TelegramAPIError as exc:
        logger.warning(
            "Could not create a support topic for user %s in chat %s — check that "
            "topics are enabled in the group and that the bot is an admin with "
            "the 'manage topics' right: %s",
            user.id,
            chat_id,
            exc,
        )
        return None
    user.support_thread_id = topic.message_thread_id
    try:
        intro = await bot.send_message(
            chat_id,
            texts.THREAD_INTRO.format(client=_client(user), user_id=user.id),
            parse_mode="HTML",
            message_thread_id=topic.message_thread_id,
        )
        await repo.remember_relay(session, intro.message_id, user.id)
    except TelegramAPIError as exc:
        logger.warning("Could not post thread intro for user %s: %s", user.id, exc)
    return topic.message_thread_id


async def _deliver(
    bot: Bot,
    session: AsyncSession,
    user: User,
    chat_id: int | None,
    thread_id: int | None,
    action: Callable[[int | None], Awaitable[Message]],
) -> Message:
    """Run ``action(thread_id)``, retrying once if the stored thread is stale.

    If the client's topic was deleted from Telegram, sending into it raises
    "message thread not found". We then drop the stale id, create a fresh
    topic and re-send. Non-thread errors are propagated to the caller.
    """
    try:
        return await action(thread_id)
    except TelegramBadRequest as exc:
        if (
            thread_id is not None
            and user.support_thread_id is not None
            and _is_stale_thread(exc)
        ):
            logger.warning(
                "Stored support thread %s for user %s is gone; recreating it.",
                thread_id,
                user.id,
            )
            user.support_thread_id = None
            fresh = await ensure_user_thread(bot, session, user)
            if fresh is None or fresh == thread_id:
                raise exc
            user.support_thread_id = fresh
            return await action(fresh)
        raise


async def _support_destination(
    bot: Bot, session: AsyncSession, user: User
) -> tuple[int | None, int | None]:
    """Resolve the single shared support thread for this client."""
    settings = get_settings()
    if settings.support_chat_id is not None:
        thread = await ensure_user_thread(bot, session, user)
        return settings.support_chat_id, thread
    admins = settings.admin_ids
    return (admins[0] if admins else None), None


async def _send_to_support(
    bot: Bot, session: AsyncSession, text: str, user: User, **kwargs
) -> Message | None:
    """Send to the client's general support thread (or admin DM fallback)."""
    chat_id, thread_id = await _support_destination(bot, session, user)
    if chat_id is None:
        logger.warning("No support target configured; dropping notification")
        return None

    async def action(t: int | None) -> Message:
        send_kwargs = dict(kwargs)
        if t is not None:
            send_kwargs["message_thread_id"] = t
        return await bot.send_message(chat_id, text, parse_mode="HTML", **send_kwargs)

    try:
        sent = await _deliver(bot, session, user, chat_id, thread_id, action)
    except TelegramAPIError as exc:
        logger.warning(
            "Could not deliver a notification for user %s to %s (thread %s): %s",
            user.id,
            chat_id,
            thread_id,
            exc,
        )
        return None
    await repo.remember_relay(session, sent.message_id, user.id)
    return sent


async def send_to_client_thread(
    bot: Bot,
    session: AsyncSession,
    text: str,
    user: User,
    **kwargs,
) -> Message | None:
    """Send the message to the client's shared support thread.

    Falls back to the admin DM when the support chat is not a forum.
    """
    thread_id = await ensure_user_thread(bot, session, user)
    if thread_id is not None:
        chat_id = get_settings().support_chat_id

        async def action(t: int) -> Message:
            return await bot.send_message(
                chat_id, text, parse_mode="HTML", message_thread_id=t, **kwargs
            )

        try:
            sent = await _deliver(bot, session, user, chat_id, thread_id, action)
        except TelegramAPIError as exc:
            logger.warning(
                "Could not deliver a notification for user %s to %s (thread %s): %s",
                user.id,
                chat_id,
                thread_id,
                exc,
            )
            return None
        await repo.remember_relay(session, sent.message_id, user.id)
        return sent
    return await _send_to_support(bot, session, text, user, **kwargs)


async def notify_new_payment(bot: Bot, session: AsyncSession, user: User, order: Order) -> None:
    text = texts.ADMIN_NEW_PAYMENT.format(
        order_id=order.id,
        title=_esc(order.title),
        code=order.article or "—",
        price=order.price,
        client=_client(user),
    )
    await send_to_client_thread(
        bot, session, text, user, reply_markup=inline.admin_payment(order.id)
    )


async def notify_new_personal(bot: Bot, session: AsyncSession, user: User, order: Order) -> None:
    text = texts.ADMIN_NEW_PERSONAL.format(
        order_id=order.id, client=_client(user), description=_esc(order.description)
    )
    await send_to_client_thread(bot, session, text, user)


async def notify_new_order(bot: Bot, session: AsyncSession, user: User, order: Order) -> None:
    """Announce a support-created order in the client's shared thread."""
    text = texts.ADMIN_NEW_ORDER.format(
        order_id=order.id,
        title=_esc(order.title),
        price=order.price,
        client=_client(user),
    )
    await send_to_client_thread(bot, session, text, user)


async def post_order_panel(bot: Bot, session: AsyncSession, user: User, order: Order) -> None:
    """Post the order's status control panel into the client's shared thread."""
    text = texts.ORDER_PANEL.format(
        order_id=order.id,
        title=_esc(order.title) or "Заказ",
        status=status_label(order.status),
    )
    await send_to_client_thread(
        bot, session, text, user, reply_markup=inline.order_status(order.id, order.status)
    )


async def _send_avito(user: User, text: str) -> bool:
    """Deliver a plain-text message to an Avito client. True when sent."""
    if not user.avito_chat_id:
        return False
    try:
        await get_avito().send_message(user.avito_chat_id, text)
        return True
    except Exception as exc:
        logger.warning(
            "Could not deliver an Avito message to chat %s: %s", user.avito_chat_id, exc
        )
        return False


def _is_bad_file_id(exc: TelegramAPIError) -> bool:
    return "file identifier" in str(exc).lower()


async def notify_order_status(
    bot: Bot, user_id: int, order: Order, session: AsyncSession | None = None
) -> None:
    """Notify the client that their order status changed.

    Telegram clients get the status banner (via its cached file_id when
    possible — pass ``session`` to enable that cache); Avito clients get a
    plain text message.
    """
    user = await repo.get_user(session, user_id) if session is not None else None
    if user is not None and user.source == "avito":
        await _send_avito(
            user,
            texts.AVITO_STATUS_NOTIFY.format(
                order_id=order.id, status=status_label(order.status)
            ),
        )
        return

    banner = Banner.STATUS
    caption = texts.ORDER_STATUS_NOTIFY.format(
        order_id=order.id, status=status_label(order.status)
    )
    cached_id = (
        await repo.get_cached_file_id(session, banner.value) if session is not None else None
    )
    try:
        sent = await bot.send_photo(
            user_id,
            photo=cached_id or FSInputFile(banner.path),
            caption=caption,
            parse_mode="HTML",
        )
    except TelegramBadRequest as exc:
        if cached_id is None or not _is_bad_file_id(exc):
            raise
        await repo.delete_cached_file_id(session, banner.value)
        sent = await bot.send_photo(
            user_id,
            photo=FSInputFile(banner.path),
            caption=caption,
            parse_mode="HTML",
        )
    if session is not None and sent.photo:
        await repo.set_cached_file_id(session, banner.value, sent.photo[-1].file_id)


async def send_avito_invoice(bot: Bot, session: AsyncSession, user: User, order: Order) -> None:
    """Send an invoice to an Avito client + post the confirm/reject panel.

    Avito clients cannot press "Я оплатил", so support confirms the payment
    manually via the buttons posted into the client's thread.
    """
    description = f"{order.description}\n" if order.description else ""
    await _send_avito(
        user,
        texts.AVITO_INVOICE.format(
            title=order.title or "Заказ",
            description=description,
            price=order.price or 0,
            requisites=requisites_plain(),
        ),
    )
    await send_to_client_thread(
        bot,
        session,
        texts.ADMIN_PAYMENT_CONTROL.format(order_id=order.id),
        user,
        reply_markup=inline.admin_payment(order.id),
    )


async def send_avito_payment_decision(user: User, order: Order, *, approved: bool) -> None:
    text = texts.AVITO_APPROVED if approved else texts.AVITO_REJECTED
    await _send_avito(user, text.format(order_id=order.id))


async def notify_dialog_closed(bot: Bot, session: AsyncSession, user: User) -> None:
    """Tell the support thread that the client ended the dialog."""
    text = texts.ADMIN_DIALOG_CLOSED.format(
        client=_client(user), order_id=user.current_order_id or 0
    )
    await send_to_client_thread(bot, session, text, user)


async def relay_user_to_support(
    bot: Bot, session: AsyncSession, user: User, message: Message
) -> bool:
    chat_id, thread_id = await _support_destination(bot, session, user)
    if chat_id is None:
        return False

    order_id = user.current_order_id or 0
    header = texts.ADMIN_DIALOG_MSG.format(client=_client(user), order_id=order_id)

    async def send_header(t: int | None) -> Message:
        if t is not None:
            return await bot.send_message(
                chat_id, header, parse_mode="HTML", message_thread_id=t
            )
        return await bot.send_message(chat_id, header, parse_mode="HTML")

    async def copy_content(t: int | None) -> Message:
        if t is not None:
            return await bot.copy_message(
                chat_id, message.chat.id, message.message_id, message_thread_id=t
            )
        return await bot.copy_message(chat_id, message.chat.id, message.message_id)

    try:
        sent = await _deliver(bot, session, user, chat_id, thread_id, send_header)
        await repo.remember_relay(session, sent.message_id, user.id)
        # The topic may have just been recreated — copy into the same one.
        copied = await _deliver(
            bot, session, user, chat_id, user.support_thread_id or thread_id, copy_content
        )
        await repo.remember_relay(session, copied.message_id, user.id)
        return True
    except TelegramAPIError as exc:
        logger.warning(
            "Could not relay a message from user %s to %s (thread %s): %s",
            user.id,
            chat_id,
            thread_id,
            exc,
        )
        return False


async def relay_admin_to_user(bot: Bot, user: User, message: Message) -> None:
    """Deliver a support reply to the client (Telegram chat or Avito chat)."""
    if user.source == "avito":
        text = message.html_text or message.caption or ""
        if text:
            text = f"{texts.AVITO_REPLY_PREFIX}{_plain(text)}"
        else:
            text = texts.AVITO_ATTACHMENT.format(kind="вложение")
        await _send_avito(user, text)
        return
    if message.text:
        await bot.send_message(
            user.id, f"{texts.DIALOG_REPLY_PREFIX}{message.html_text}", parse_mode="HTML"
        )
    else:
        await bot.copy_message(user.id, message.chat.id, message.message_id)
