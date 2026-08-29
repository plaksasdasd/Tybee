"""HTTP server receiving Avito Messenger webhooks.

Avito POSTs every chat event to the subscription URL. We only care about
incoming client messages: each Avito client is mapped to a User row and gets
their own support thread in Telegram (exactly like Telegram clients), where
their messages appear and from where support replies / invoices / status
updates are delivered back to the Avito chat.

Run together with the bot polling loop (see app.bot.run).
"""
from __future__ import annotations

import asyncio
import html
import logging
from collections import deque

from aiohttp import web
from aiogram import Bot

from app import repositories as repo
from app import texts
from app.config import get_settings
from app.db.base import session_factory
from app.services import notify
from app.services.avito import AvitoError, get_avito

logger = logging.getLogger(__name__)

# Webhooks are delivered at-least-once — keep a bounded set of handled ids.
_SEEN_LIMIT = 4096
_seen: deque[str] = deque(maxlen=_SEEN_LIMIT)
_seen_set: set[str] = set()


def _remember(message_id: str) -> bool:
    """True if this message id was not processed before."""
    if message_id in _seen_set:
        return False
    if len(_seen) == _seen.maxlen:
        _seen_set.discard(_seen[0])
    _seen.append(message_id)
    _seen_set.add(message_id)
    return True


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
    author_id = int(author_id)

    avito = get_avito()
    # Our own outgoing messages are echoed back by the webhook — ignore them.
    if avito.account_id is not None and author_id == avito.account_id:
        return

    body = _extract_text(value)
    if not body:
        return

    try:
        async with session_factory() as session:
            user = await repo.get_or_create_avito_user(session, author_id, chat_id)
            # Fill in the real name once, right after the first message.
            if user.full_name.startswith("Avito "):
                name = await avito.get_chat_user_name(chat_id)
                if name:
                    user.full_name = name
            if user.is_banned:
                await session.commit()
                return
            await notify.ensure_user_thread(bot, session, user)
            header = texts.ADMIN_AVITO_MSG.format(
                client=repo.display_name(user),
                order_id=user.current_order_id or 0,
            )
            await notify.send_to_client_thread(
                bot, session, f"{header}\n{html.escape(body)}", user
            )
            await session.commit()
    except AvitoError as exc:
        logger.warning("Avito API error while relaying chat %s: %s", chat_id, exc)
    except Exception:
        logger.exception("Failed to relay Avito message from chat %s", chat_id)


async def _safe_process(bot: Bot, value: dict) -> None:
    try:
        await process_avito_message(bot, value)
    except Exception:
        logger.exception("Unexpected error in Avito webhook processing")


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
    if message_id and not _remember(message_id):
        return web.json_response({"ok": True})

    bot: Bot = request.app["bot"]
    # Answer Avito immediately; process asynchronously.
    asyncio.create_task(_safe_process(bot, value))
    return web.json_response({"ok": True})


async def start_avito_webhook(bot: Bot) -> web.AppRunner | None:
    """Start the webhook HTTP server and subscribe the URL, if configured.

    Returns the runner (caller must ``.cleanup()`` it on shutdown) or ``None``
    when the Avito integration is disabled / incomplete.
    """
    settings = get_settings()
    avito = get_avito()
    if not avito.enabled:
        return None
    if not settings.avito_webhook_url:
        logger.warning(
            "Avito credentials are set but AVITO_WEBHOOK_URL is empty — incoming "
            "Avito messages will NOT be received. Set it to the public HTTPS "
            "address of this machine."
        )
        return None

    app = web.Application()
    app["bot"] = bot
    app.router.add_post(settings.avito_webhook_path, handle_webhook)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.avito_webhook_port)
    await site.start()
    logger.info(
        "Avito webhook server listening on 0.0.0.0:%s%s",
        settings.avito_webhook_port,
        settings.avito_webhook_path,
    )

    try:
        await avito.subscribe_webhook(settings.avito_subscription_url)
    except Exception as exc:
        logger.error(
            "Could not subscribe the Avito webhook (%s): %s — incoming messages "
            "will not arrive until subscription succeeds",
            settings.avito_subscription_url,
            exc,
        )
    return runner
