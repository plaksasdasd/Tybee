from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections import defaultdict, deque

from aiohttp import web

from app.config import get_settings
from app.db.base import session_factory
from app.robokassa_webhook import handle_robokassa_result
from app.services.avito import get_avito
from app.services.robokassa import robokassa_ready
from app.services.support_transport import get_or_create_avito_user, queue_support_message

logger = logging.getLogger(__name__)
_RATE_WINDOW = 60
_RATE_LIMIT = 120
_requests: dict[str, deque[float]] = defaultdict(deque)
_webhook_concurrency = asyncio.Semaphore(20)


def _avito_webhook_ready() -> bool:
    settings = get_settings()
    return bool(
        get_avito().enabled
        and settings.avito_webhook_url.strip()
        and settings.avito_webhook_secret.strip()
    )


@web.middleware
async def rate_limit(request: web.Request, handler) -> web.Response:
    now = time.monotonic()
    key = request.remote or "unknown"
    attempts = _requests[key]
    while attempts and attempts[0] <= now - _RATE_WINDOW:
        attempts.popleft()
    if len(attempts) >= _RATE_LIMIT:
        return web.json_response({"error": "rate limit exceeded"}, status=429)
    attempts.append(now)
    return await handler(request)


def _extract_text(value: dict) -> str:
    content = value.get("content") or {}
    kind = content.get("type")
    if kind == "text":
        return (content.get("text") or "").strip()
    if kind is None:
        return ""
    return f"[вложение: {kind}]"


async def process_avito_message(value: dict) -> None:
    chat_id = str(value.get("chat_id") or "")
    author_id = value.get("author_id") or value.get("user_id")
    body = _extract_text(value)
    if not chat_id or author_id is None or not body:
        return
    try:
        author_id = int(author_id)
    except (TypeError, ValueError):
        logger.warning("Ignoring Avito message with invalid author_id: %r", author_id)
        return
    avito = get_avito()
    if avito.account_id is not None and author_id == avito.account_id:
        return

    async with session_factory() as session:
        user = await get_or_create_avito_user(session, author_id, chat_id)
        if user.full_name.startswith("Avito "):
            name = await avito.get_chat_user_name(chat_id)
            if name:
                user.full_name = name
        if user.is_banned:
            await session.commit()
            return
        await queue_support_message(
            session,
            user,
            body,
            order_id=user.current_order_id,
            external_id=f"avito:{value.get('id')}",
        )
        await session.commit()


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def handle_webhook(request: web.Request) -> web.Response:
    if not _avito_webhook_ready() or not secrets.compare_digest(
        request.path, get_settings().avito_webhook_path
    ):
        return web.json_response({"error": "not found"}, status=404)
    async with _webhook_concurrency:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)
        payload = data.get("payload") or {}
        if payload.get("type") != "message":
            return web.json_response({"ok": True})
        value = payload.get("value") or {}
        if not value.get("id"):
            return web.json_response({"error": "missing message id"}, status=400)
        try:
            await process_avito_message(value)
        except Exception:
            logger.exception("Could not persist Avito webhook %s", value.get("id"))
            return web.json_response({"error": "temporary storage error"}, status=503)
        return web.json_response({"ok": True})


def create_web_application() -> web.Application:
    app = web.Application(client_max_size=1_048_576, middlewares=[rate_limit])
    app.router.add_get("/health", health)
    if _avito_webhook_ready():
        app.router.add_post(get_settings().avito_webhook_path, handle_webhook)
    if robokassa_ready():
        app.router.add_get("/robokassa/result", handle_robokassa_result)
        app.router.add_post("/robokassa/result", handle_robokassa_result)
    return app


async def start_web_server() -> web.AppRunner | None:
    settings = get_settings()
    avito_ready = _avito_webhook_ready()
    if not get_avito().enabled and not robokassa_ready():
        return None
    if get_avito().enabled and not avito_ready:
        logger.error("Avito webhook requires both AVITO_WEBHOOK_URL and AVITO_WEBHOOK_SECRET")
        if not robokassa_ready():
            return None
    runner = web.AppRunner(create_web_application())
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.avito_webhook_port)
    await site.start()
    logger.info("Webhook server listening on port %s", settings.avito_webhook_port)
    if avito_ready:
        try:
            await get_avito().subscribe_webhook(settings.avito_subscription_url)
        except Exception as exc:
            logger.error("Could not subscribe the Avito webhook: %s", exc)
    return runner
