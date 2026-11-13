"""Sends broadcasts created on the site (ТЗ-2, D.3). Runs in the bot process.

* takes a ``queued`` (or a ``running`` one left by a restart) broadcast with
  ``FOR UPDATE SKIP LOCKED``;
* walks recipients by ``user_id`` after ``cursor_user_id``, saving the cursor after
  every message, so a restart never re-sends;
* at most 25 messages per second in total; ``TelegramRetryAfter`` is waited out;
* a recipient whose cover is not running is counted as failed (with the reason);
* ``cancelled`` (set by the site) is checked every 50 messages.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import FSInputFile
from sqlalchemy import case, select

from app.catalog.broadcast import Recipient, recipients, resolve_cover_ids
from app.covers import registry
from app.db import base as db_base
from app.db.models import Broadcast, Cover
from app.support import attachments as files

logger = logging.getLogger(__name__)

RATE_PER_SEC = 25
CANCEL_CHECK_EVERY = 50
BATCH = 50
MAX_RETRY_AFTER = 5

BotProvider = Callable[[int], Bot | None]
Sleeper = Callable[[float], Awaitable[None]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def next_broadcast_id() -> int | None:
    async with db_base.session_factory() as session:
        return await session.scalar(
            select(Broadcast.id)
            .where(Broadcast.status.in_(("running", "queued")))
            .order_by(case((Broadcast.status == "running", 0), else_=1), Broadcast.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )


async def _send(
    bot: Bot, r: Recipient, body: str, image, file_ids: dict[int, str], sleep: Sleeper
) -> str | None:
    """None when delivered, otherwise the failure reason."""
    for _ in range(MAX_RETRY_AFTER + 1):
        try:
            if image is not None:
                photo = file_ids.get(r.cover_id) or FSInputFile(image)
                sent = await bot.send_photo(r.user_id, photo, caption=body, parse_mode="HTML")
                if r.cover_id not in file_ids and getattr(sent, "photo", None):
                    file_ids[r.cover_id] = sent.photo[-1].file_id
            else:
                await bot.send_message(r.user_id, body, parse_mode="HTML")
            return None
        except TelegramRetryAfter as exc:
            logger.warning("Broadcast: flood control, waiting %s s", exc.retry_after)
            await sleep(float(exc.retry_after))
        except TelegramForbiddenError:
            return "клиент заблокировал бота"
        except TelegramAPIError as exc:
            logger.info("Broadcast message to %s failed: %s", r.user_id, type(exc).__name__)
            return "ошибка Telegram"
    return "Telegram просит подождать слишком долго"


async def run_broadcast(broadcast_id: int, bot_for: BotProvider | None = None, sleep: Sleeper = asyncio.sleep) -> str:
    """Send (or resume) one broadcast. Returns its final status."""
    bot_for = bot_for or registry.get_bot
    async with db_base.session_factory() as session:
        b = await session.get(Broadcast, broadcast_id)
        if b is None or b.status not in ("queued", "running"):
            return b.status if b else "missing"
        covers = await resolve_cover_ids(session, b.cover_ids or None)
        if b.status == "queued":
            b.status = "running"
            b.started_at = _now()
            b.total = len(await recipients(session, covers))
            await session.commit()
        body, image_rel = b.body, b.image_path
        cursor = b.cursor_user_id
        slugs = dict((await session.execute(select(Cover.id, Cover.slug))).all())
    image = files.resolve(image_rel) if image_rel else None
    if image_rel and image is None:
        return await _finish(broadcast_id, "failed", "картинка рассылки не найдена")

    file_ids: dict[int, str] = {}
    since_check = 0
    interval = 1.0 / RATE_PER_SEC
    last_send = 0.0
    while True:
        async with db_base.session_factory() as session:
            batch = await recipients(session, covers, after_user_id=cursor, limit=BATCH)
        if not batch:
            break
        for r in batch:
            if since_check >= CANCEL_CHECK_EVERY:
                since_check = 0
                async with db_base.session_factory() as session:
                    if await session.scalar(select(Broadcast.status).where(Broadcast.id == broadcast_id)) == "cancelled":
                        logger.info("Broadcast %s cancelled", broadcast_id)
                        return "cancelled"
            bot = bot_for(r.cover_id)
            if bot is None:
                reason = f"обложка {slugs.get(r.cover_id, r.cover_id)} не запущена"
            else:
                wait = interval - (time.monotonic() - last_send)
                if wait > 0:
                    await sleep(wait)
                last_send = time.monotonic()
                reason = await _send(bot, r, body, image, file_ids, sleep)
            async with db_base.session_factory() as session:
                b = await session.get(Broadcast, broadcast_id)
                if reason is None:
                    b.sent = (b.sent or 0) + 1
                else:
                    b.failed = (b.failed or 0) + 1
                    reasons = dict(b.fail_reasons or {})
                    reasons[reason] = reasons.get(reason, 0) + 1
                    b.fail_reasons = reasons
                b.cursor_user_id = r.user_id
                await session.commit()
            cursor = r.user_id
            since_check += 1
    return await _finish(broadcast_id, "done")


async def _finish(broadcast_id: int, status: str, reason: str | None = None) -> str:
    async with db_base.session_factory() as session:
        b = await session.get(Broadcast, broadcast_id)
        if b.status == "cancelled":
            return "cancelled"
        b.status = status
        b.finished_at = _now()
        if reason:
            b.fail_reasons = {**(b.fail_reasons or {}), reason: 1}
        await session.commit()
    logger.info("Broadcast %s finished: %s", broadcast_id, status)
    return status


async def broadcast_worker(bot_for: BotProvider | None = None, interval: float = 2.0) -> None:
    while True:
        broadcast_id = None
        try:
            broadcast_id = await next_broadcast_id()
            if broadcast_id is not None:
                await run_broadcast(broadcast_id, bot_for)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Broadcast worker iteration failed")
            if broadcast_id is not None:
                try:
                    await _finish(broadcast_id, "failed", "внутренняя ошибка")
                except Exception:  # noqa: BLE001
                    logger.exception("Could not mark broadcast %s as failed", broadcast_id)
        if broadcast_id is None:
            await asyncio.sleep(interval)
