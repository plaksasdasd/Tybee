from __future__ import annotations

import asyncio
import html
import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone, timedelta

from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import FSInputFile
from sqlalchemy import func, or_, select

from app import repositories as repo
from app import texts
from app.covers import registry
from app.covers.loader import cover_status_title, cover_text
from app.db.base import session_factory
from app.db.models import (
    AvitoChat,
    AvitoProfile,
    Cover,
    Order,
    SupportAttachment,
    SupportOutbox,
    SupportThread,
    SupportThreadMessage,
    User,
)
from app.keyboards import inline
from app.services.avito import AvitoClient, AvitoProfileUnavailable, client_for_profile
from app.services.notify import notify_undeliverable, resolve_reply_cover
from app.services.robokassa import payment_link
from app.services.support_transport import get_open_conversation
from app.states import UserState
from app.support import attachments as files

logger = logging.getLogger(__name__)
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")

# A cover that should be running but is not yet (startup, restart after a token
# change) gets this long before its queued messages are declared undeliverable.
_COVER_START_GRACE_SEC = 120
_BATCH = 50
_CAPTION_LIMIT = 1024

BotProvider = Callable[[int | None], Bot | None]


def _plain(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text))


class Undeliverable(Exception):
    """Permanent failure: the event is closed and the operator gets "не доставлено: <reason>"."""


Attachment = tuple[SupportAttachment, Path | None]


async def _load_attachment(session, event: SupportOutbox) -> Attachment | None:
    if event.attachment_id is None:
        return None
    row = await session.get(SupportAttachment, event.attachment_id)
    path = files.resolve(row.path) if row is not None else None
    if row is None or (path is None and not event.attachment_sent):
        raise Undeliverable("файл вложения не найден")
    return row, path


async def _staff_text(session, event: SupportOutbox) -> str:
    """What the operator typed next to a file (empty when only the file was sent)."""
    body = await session.scalar(
        select(SupportThreadMessage.body).where(SupportThreadMessage.outbox_id == event.id).limit(1)
    )
    return (body or "").strip()


async def resolve_avito_chat(session, event: SupportOutbox, user: User) -> AvitoChat | None:
    """ТЗ-2 C.5: ``event.avito_chat_ref`` -> ``thread.last_avito_chat_ref`` -> the client's latest chat."""
    for ref in (
        event.avito_chat_ref,
        await session.scalar(select(SupportThread.last_avito_chat_ref).where(SupportThread.user_id == user.id)),
    ):
        if ref is not None:
            chat = await session.get(AvitoChat, ref)
            if chat is not None and chat.user_id == user.id:
                return chat
    return await session.scalar(
        select(AvitoChat)
        .where(AvitoChat.user_id == user.id)
        .order_by(AvitoChat.last_message_at.desc().nulls_last(), AvitoChat.id.desc())
        .limit(1)
    )


def get_avito_client(profile: AvitoProfile) -> AvitoClient:
    """Indirection so tests can swap the API client."""
    return client_for_profile(profile)


async def _avito_target(session, event: SupportOutbox, user: User, order: Order | None) -> tuple[AvitoClient, str]:
    """(client of the chat's own profile, chat_id). Never falls back to another profile."""
    chat = await resolve_avito_chat(session, event, user)
    if chat is None:
        raise Undeliverable("у клиента нет чата Avito")
    profile = await session.get(AvitoProfile, chat.profile_id)
    if profile is None:
        raise Undeliverable("профиль Avito не найден")
    try:
        client = get_avito_client(profile)
    except AvitoProfileUnavailable as exc:
        raise Undeliverable(str(exc)) from None
    if event.kind == "invoice" and order is not None and order.avito_profile_id is None:
        order.avito_profile_id = profile.id
    return client, chat.chat_id


async def _deliver_avito_file(
    avito: AvitoClient, chat_id: str, event: SupportOutbox, attachment: Attachment, text: str
) -> None:
    row, path = attachment
    if row.mime not in files.INLINE_MIMES:
        raise Undeliverable("Avito принимает только изображения")
    if not event.attachment_sent:
        if path is None:
            raise Undeliverable("файл вложения не найден")
        image_id = await avito.upload_image(path.read_bytes(), row.original_name or path.name, row.mime)
        sent = await avito.send_image(chat_id, image_id)
        if isinstance(sent, dict):
            from app.avito_webhook import _largest_image_url
            url = _largest_image_url(sent.get("content") or {})
            if url:
                row.remote_provider, row.remote_id = "avito_image", url
        event.attachment_sent = True
    if text and not event.body_sent:
        await avito.send_message(chat_id, _plain(event.body))
        event.body_sent = True


async def _deliver_avito(
    session, event: SupportOutbox, user: User, order: Order | None, attachment: Attachment | None = None,
    text: str = "",
) -> None:
    avito, chat_id = await _avito_target(session, event, user, order)
    if attachment is not None:
        await _deliver_avito_file(avito, chat_id, event, attachment, text)
        return
    if event.kind == "invoice" and order is not None:
        url = payment_link(order)
        if url is None:
            body = texts.PAYMENT_UNAVAILABLE
        else:
            description = f"{order.description}\n" if order.description else ""
            body = texts.AVITO_INVOICE.format(
                title=order.title or "Заказ",
                description=description,
                price=order.price or 0,
            )
            body += f"\n\nСсылка на оплату: {url}"
    elif event.kind == "ping" and order is not None:
        body = texts.AVITO_PING.format(
            order_id=order.id,
            title=order.title or "Заказ",
            status=texts.STATUS_LABELS.get(order.status, order.status),
        )
        url = payment_link(order) if order.status == "awaiting_payment" else None
        if url:
            body += f"\n\nСсылка на оплату: {url}"
    else:
        body = _plain(event.body)
    await avito.send_message(chat_id, body)


async def _deliver_telegram_file(bot: Bot, user: User, event: SupportOutbox, attachment: Attachment) -> None:
    """A photo or a document with the operator's caption; a caption over 1024
    characters goes as a separate message after the file."""
    row, path = attachment
    as_photo = row.mime in files.INLINE_MIMES
    caption = event.body or None
    if caption is not None and len(_plain(caption)) > _CAPTION_LIMIT:
        caption = None
    if not event.attachment_sent:
        if path is None:
            raise Undeliverable("файл вложения не найден")
        upload = FSInputFile(path, filename=row.original_name or path.name)
        if as_photo:
            sent = await bot.send_photo(user.id, upload, caption=caption, parse_mode="HTML" if caption else None)
        else:
            sent = await bot.send_document(user.id, upload, caption=caption, parse_mode="HTML" if caption else None)
        media = getattr(sent, "photo", None) if as_photo else getattr(sent, "document", None)
        media = media[-1] if as_photo and isinstance(media, (list, tuple)) and media else media
        file_id = getattr(media, "file_id", None)
        if isinstance(file_id, str) and file_id:
            row.remote_provider, row.remote_id = "telegram", file_id
        event.attachment_sent = True
        event.body_sent = caption is not None
    if not event.body_sent and event.body:
        await bot.send_message(user.id, event.body, parse_mode="HTML")
        event.body_sent = True


async def _deliver_telegram(
    bot: Bot, cid: int, event: SupportOutbox, user: User, order: Order | None,
    attachment: Attachment | None = None,
) -> None:
    """Send one queued message to a Telegram client through the cover's bot."""
    if attachment is not None:
        await _deliver_telegram_file(bot, user, event, attachment)
        return
    if event.kind == "invoice" and order is not None:
        url = payment_link(order)
        if url is None:
            await bot.send_message(
                user.id,
                cover_text(cid, "PAYMENT_UNAVAILABLE"),
                reply_markup=inline.back_to_menu(cid),
            )
            return
        description = f"{html.escape(order.description)}\n" if order.description else ""
        body = cover_text(
            cid,
            "ORDER_CREATED",
            title=html.escape(order.title or ""),
            description=description,
            price=order.price,
        )
        await bot.send_message(
            user.id,
            body,
            parse_mode="HTML",
            reply_markup=inline.payment_actions(cid, url),
        )
        return
    if event.kind == "approval" and order is not None:
        await bot.send_message(
            user.id,
            cover_text(cid, "APPROVED", order_id=order.id),
            parse_mode="HTML",
            reply_markup=inline.open_dialog(cid, order.id),
        )
        return
    if event.kind.startswith("status:") and order is not None:
        title = cover_status_title(cid, event.kind.split(":", 1)[1])
        await bot.send_message(
            user.id,
            cover_text(cid, "ORDER_STATUS_PUSH", order_id=order.id, title=title),
            parse_mode="HTML",
        )
        return
    if event.kind == "unbanned":
        await bot.send_message(user.id, cover_text(cid, "UNBANNED"), parse_mode="HTML")
        return
    if event.kind == "ping" and order is not None:
        text = cover_text(
            cid,
            "ORDER_PING",
            order_id=order.id,
            title=html.escape(order.title or cover_text(cid, "ORDER_DEFAULT_TITLE")),
            status=cover_status_title(cid, order.status),
        )
        markup = None
        if order.status == "awaiting_payment":
            url = payment_link(order)
            markup = inline.payment_reminder(cid, url)
        await bot.send_message(user.id, text, parse_mode="HTML", reply_markup=markup)
        return
    await bot.send_message(user.id, event.body, parse_mode="HTML")


def _age_seconds(created_at: datetime | None) -> float:
    if created_at is None:
        return 0.0
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created_at).total_seconds()


async def cleanup_delivered_attachments(session) -> None:
    """Retry removal after a crash; only new files confirmed sent are touched."""
    rows = list(await session.scalars(
        select(SupportAttachment).join(SupportOutbox, SupportOutbox.attachment_id == SupportAttachment.id)
        .where(SupportAttachment.ephemeral.is_(True), SupportAttachment.direction == "out",
               SupportOutbox.attachment_sent.is_(True), SupportAttachment.path != "")
        .limit(100).with_for_update(skip_locked=True)
    ))
    for row in rows:
        path = files.resolve(row.path)
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove delivered attachment %s", row.id)
                continue
        row.path = ""
    if rows:
        await session.commit()


async def process_next_outbox(bot_for: BotProvider | None = None) -> bool:
    """Deliver the next deliverable outbox event. True when one was handled.

    The cover (bot) is chosen by: explicit ``event.cover_id`` -> the order's
    cover -> :func:`resolve_reply_cover`. If that cover is not running the
    message is NOT redirected to another cover: the event is closed with an
    error and the operator gets a "не доставлено" notice.
    """
    bot_for = bot_for or registry.get_bot
    async with session_factory() as session:
        events = list(
            await session.scalars(
                select(SupportOutbox)
                .where(SupportOutbox.processed_at.is_(None), or_(SupportOutbox.next_attempt_at.is_(None), SupportOutbox.next_attempt_at <= func.now()))
                .order_by(SupportOutbox.id)
                .limit(_BATCH)
                .with_for_update(skip_locked=True)
            )
        )
        for event in events:
            user = await session.get(User, event.user_id)
            order = (
                await session.get(Order, event.order_id) if event.order_id is not None else None
            )
            delivered = True
            try:
                if user is None:
                    raise RuntimeError(f"User {event.user_id} not found")
                try:
                    attachment = await _load_attachment(session, event)
                    if user.source == "avito":
                        text = await _staff_text(session, event) if attachment is not None else ""
                        await _deliver_avito(session, event, user, order, attachment, text)
                    else:
                        outcome = await _route_telegram(session, bot_for, event, user, order, attachment)
                        if outcome == "wait":
                            continue
                        delivered = outcome == "sent"
                except (Undeliverable, TelegramBadRequest) as exc:
                    delivered = False
                    await _undeliverable(session, event, user, str(exc))
            except TelegramRetryAfter as exc:
                event.attempts += 1
                event.last_error = f"Канал ограничил частоту отправки; повтор через {exc.retry_after} с"
                event.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=exc.retry_after)
                if event.attempts >= 5:
                    event.processed_at = func.now()
                await session.commit()
                await cleanup_delivered_attachments(session)
                continue
            except Exception as exc:
                # A timeout may happen after the provider accepted the message.
                # Stop blind retries; the operator explicitly confirms duplicate risk.
                event.attempts += 1
                event.last_error = f"Результат отправки неизвестен: {str(exc)[:3800]}"
                event.delivery_uncertain = True
                event.processed_at = func.now()
                await session.commit()
                await cleanup_delivered_attachments(session)
                logger.warning("Could not confirm outbox event %s: %s", event.id, exc)
                continue
            if delivered:
                event.last_error = None
                event.delivery_uncertain = False
                event.next_attempt_at = None
                message = await session.scalar(select(SupportThreadMessage).where(SupportThreadMessage.outbox_id == event.id, SupportThreadMessage.direction == "staff"))
                if message is not None:
                    thread = await session.scalar(select(SupportThread).where(SupportThread.id == message.thread_id).with_for_update())
                    newer = await session.scalar(select(func.min(SupportThreadMessage.created_at)).where(
                        SupportThreadMessage.thread_id == message.thread_id, SupportThreadMessage.direction == "client", SupportThreadMessage.id > message.id))
                    if thread is not None:
                        thread.needs_reply = newer is not None
                        thread.waiting_since = newer
            event.processed_at = func.now()
            await session.commit()
            await cleanup_delivered_attachments(session)
            return True
        return False


async def _route_telegram(
    session, bot_for: BotProvider, event: SupportOutbox, user: User, order: Order | None,
    attachment: Attachment | None = None,
) -> str:
    """Returns "sent", "failed" (closed with a notice) or "wait" (cover starting)."""
    cid = event.cover_id or (order.cover_id if order is not None else None)
    if cid is None:
        cid = await resolve_reply_cover(session, user.id)
    if cid is None:
        return await _undeliverable(session, event, user, "у клиента нет обложки")

    bot = bot_for(cid)
    if bot is None:
        cover = await session.get(Cover, cid)
        slug = cover.slug if cover is not None else str(cid)
        if cover is None or not cover.enabled:
            return await _undeliverable(session, event, user, f"обложка {slug} отключена")
        if cover.last_error is None and _age_seconds(event.created_at) < _COVER_START_GRACE_SEC:
            return "wait"
        return await _undeliverable(session, event, user, f"обложка {slug} недоступна")

    if order is not None and order.cover_id is None:
        order.cover_id = cid  # e.g. an invoice created by support: bind to the delivering cover
    if event.kind == "invoice" and order is not None:
        uc = await repo.get_or_create_user_cover(session, user, cid)
        uc.current_order_id = order.id
    try:
        if attachment is not None:
            attachment[0].remote_cover_id = cid
        await _deliver_telegram(bot, cid, event, user, order, attachment)
    except TelegramForbiddenError as exc:
        # Telegram tells us *why* the bot cannot write; the operator needs the
        # difference between "blocked", "never pressed /start" and a deleted account.
        detail = str(exc).lower()
        if "deactivated" in detail:
            reason = "аккаунт клиента удалён"
        elif "initiate" in detail or "can't initiate" in detail:
            reason = "клиент не нажимал /start"
        elif "blocked" in detail:
            reason = "клиент заблокировал бота"
        else:
            reason = "клиент заблокировал бота"
        return await _undeliverable(session, event, user, reason)
    # A new outgoing message is now below the previous menu in Telegram.
    # The next navigation must create a fresh screen below that message.
    uc = await repo.get_or_create_user_cover(session, user, cid)
    uc.screen_message_id = None
    # Staff can start a conversation while the client is browsing the menu.
    # Their next message belongs to support, rather than the menu fallback.
    if event.kind in {"text", "reply"} and await get_open_conversation(session, user) is not None:
        await repo.set_state(session, uc, UserState.DIALOG)
    event.last_error = None
    return "sent"


async def _undeliverable(session, event: SupportOutbox, user: User, reason: str) -> str:
    logger.warning("Outbox event %s not delivered: %s", event.id, reason)
    event.last_error = f"не доставлено: {reason}"[:4000]
    await notify_undeliverable(session, user, reason, outbox_id=event.id)
    return "failed"


async def outbox_worker(bot_for: BotProvider | None = None) -> None:
    next_cleanup = 0.0
    while True:
        try:
            if asyncio.get_running_loop().time() >= next_cleanup:
                async with session_factory() as session:
                    await cleanup_delivered_attachments(session)
                next_cleanup = asyncio.get_running_loop().time() + 30
            processed = await process_next_outbox(bot_for)
        except Exception:
            logger.exception("Outbox worker iteration failed")
            processed = False
        if not processed:
            await asyncio.sleep(0.25)
