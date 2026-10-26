"""HTTP server receiving Avito Messenger webhooks.

Incoming message events are stored in PostgreSQL before Avito receives a success
response. A single background worker processes pending events and marks them as
processed, including events left unfinished by a previous process.

Run together with the bot polling loop (see app.bot.run).
"""
from __future__ import annotations

import asyncio
import html
import logging
from contextlib import suppress

from aiohttp import web
from aiogram import Bot
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app import repositories as repo
from app import texts
from app.config import get_settings
from app.db.base import session_factory
from app.db.models import AvitoWebhookEvent
from app.services import notify
from app.services.avito import get_avito

logger = logging.getLogger(__name__)

_RETRY_DELAY = 5


def _extract_text(value: dict) -> str:
    content = value.get("content") or {}
    kind = content.get("type")
    if kind == "text":
        return (content.get("text") or "").strip()
    if kind is None:
        return ""
    return texts.AVITO_ATTACHMENT.format(kind=kind)


async def process_avito_message(bot: Bot, value: dict) -> None:
    """Route one incoming Avito message into the client's support thread."""
    chat_id = str(value.get("chat_id") or "")
    if not chat_id:
        return
    author_id = value.get("author_id") or value.get("user_id")
    if author_id is None:
        return
    try:
        author_id = int(author_id)
    except (TypeError, ValueError):
        logger.warning("Ignoring Avito message with invalid author_id: %r", author_id)
        return

    avito = get_avito()
    if avito.account_id is not None and author_id == avito.account_id:
        return

    body = _extract_text(value)
    if not body:
        return

    async with session_factory() as session:
        user = await repo.get_or_create_avito_user(session, author_id, chat_id)
        if user.full_name.startswith("Avito "):
            name = await avito.get_chat_user_name(chat_id)
            if name:
                user.full_name = name
        await repo.record_support_message(
            session,
            user.id,
            "inbound",
            body,
            "avito",
            external_id=f"avito:{value.get('id')}",
        )
        if user.is_banned:
            await session.commit()
            return
        await notify.ensure_user_thread(bot, session, user)
        header = texts.ADMIN_AVITO_MSG.format(
            client=repo.display_name(user),
            order_id=user.current_order_id or 0,
        )
        sent = await notify.send_to_client_thread(
            bot, session, f"{header}\n{html.escape(body)}", user
        )
        if sent is None and not get_settings().web_panel_enabled:
            raise RuntimeError(f"Could not relay Avito message from chat {chat_id}")
        await session.commit()


async def _store_event(message_id: str, value: dict) -> None:
    async with session_factory() as session:
        statement = insert(AvitoWebhookEvent).values(
            message_id=message_id,
            payload=value,
        )
        await session.execute(
            statement.on_conflict_do_nothing(index_elements=[AvitoWebhookEvent.message_id])
        )
        await session.commit()


async def _process_next(bot: Bot) -> bool:
    async with session_factory() as session:
        event = await session.scalar(
            select(AvitoWebhookEvent)
            .where(AvitoWebhookEvent.processed_at.is_(None))
            .order_by(AvitoWebhookEvent.created_at)
            .limit(1)
        )
        if event is None:
            return False
        message_id = event.message_id
        value = dict(event.payload)

    await process_avito_message(bot, value)

    async with session_factory() as session:
        await session.execute(
            update(AvitoWebhookEvent)
            .where(AvitoWebhookEvent.message_id == message_id)
            .values(processed_at=func.now())
        )
        await session.commit()
    return True


async def _run_worker(bot: Bot, wakeup: asyncio.Event, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            processed = await _process_next(bot)
        except Exception:
            logger.exception("Failed to process an Avito webhook; it will be retried")
            processed = False
        if processed:
            continue
        wakeup.clear()
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(wakeup.wait(), timeout=_RETRY_DELAY)


class AvitoWebhookServer:
    def __init__(
        self,
        runner: web.AppRunner,
        worker: asyncio.Task[None] | None,
        wakeup: asyncio.Event,
        stop: asyncio.Event,
    ) -> None:
        self._runner = runner
        self._worker = worker
        self._wakeup = wakeup
        self._stop = stop

    async def cleanup(self) -> None:
        self._stop.set()
        self._wakeup.set()
        if self._worker is not None:
            await self._worker
        await self._runner.cleanup()


async def handle_webhook(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)

    payload = data.get("payload") or {}
    if payload.get("type") != "message":
        return web.json_response({"ok": True})
    value = payload.get("value") or {}
    message_id = str(value.get("id") or "")
    if not message_id:
        return web.json_response({"error": "missing message id"}, status=400)

    try:
        await _store_event(message_id, value)
    except Exception:
        logger.exception("Could not persist Avito webhook %s", message_id)
        return web.json_response({"error": "temporary storage error"}, status=503)

    request.app["wakeup"].set()
    return web.json_response({"ok": True})


def create_web_application(bot: Bot, bot_username: str) -> web.Application:
    from app.web_panel import health, setup_web_panel

    settings = get_settings()
    app = web.Application()
    app["bot"] = bot
    app["wakeup"] = asyncio.Event()
    app.router.add_get("/health", health)
    avito_ready = (
        get_avito().enabled
        and bool(settings.avito_webhook_url)
        and bool(settings.avito_webhook_secret.strip())
    )
    if avito_ready:
        app.router.add_post(settings.avito_webhook_path, handle_webhook)
    if settings.web_panel_enabled:
        setup_web_panel(app, bot_username)
    return app


async def start_web_server(bot: Bot, bot_username: str) -> AvitoWebhookServer | None:
    settings = get_settings()
    avito = get_avito()
    if not settings.web_panel_enabled and not avito.enabled:
        return None
    avito_ready = (
        avito.enabled
        and bool(settings.avito_webhook_url)
        and bool(settings.avito_webhook_secret.strip())
    )
    if avito.enabled and not avito_ready:
        logger.error(
            "Avito webhook is disabled: AVITO_WEBHOOK_URL and AVITO_WEBHOOK_SECRET "
            "must both be configured"
        )

    app = create_web_application(bot, bot_username)
    stop = asyncio.Event()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.avito_webhook_port)
    await site.start()
    logger.info("Web server listening on port %s", settings.avito_webhook_port)

    worker = asyncio.create_task(_run_worker(bot, app["wakeup"], stop)) if avito_ready else None
    server = AvitoWebhookServer(runner, worker, app["wakeup"], stop)
    if avito_ready:
        try:
            await avito.subscribe_webhook(settings.avito_subscription_url)
        except Exception as exc:
            logger.error(
                "Could not subscribe the Avito webhook: %s — incoming messages "
                "will not arrive until subscription succeeds",
                exc,
            )
    return server
