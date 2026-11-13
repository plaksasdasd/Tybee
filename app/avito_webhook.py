"""aiohttp server of the bot process: Avito webhooks (one route for all profiles) and Robokassa ResultURL."""
from __future__ import annotations

import asyncio
import logging
import ipaddress
from urllib.parse import urlsplit
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone

import aiohttp
from aiohttp import web
from sqlalchemy import select

from app.config import get_settings
from app.db.base import session_factory
from app.db.models import AvitoChat, AvitoProfile
from app.robokassa_webhook import handle_robokassa_result
from app.services.avito import AvitoError, client_for_profile
from app.services.avito_secrets import webhook_hash
from app.services.robokassa import robokassa_ready
from app.services.support_transport import get_or_create_avito_user, queue_support_message
from app.support.attachments import AVITO_IMAGE_LIMIT, StoredFile, discard

logger = logging.getLogger(__name__)
_RATE_WINDOW = 60
_RATE_LIMIT = 120
_requests: dict[str, deque[float]] = defaultdict(deque)
_webhook_concurrency = asyncio.Semaphore(20)
WEBHOOK_ROUTE = "/avito/webhook/{secret}"


@dataclass(frozen=True)
class ProfileRef:
    """What the message handler needs from an ``AvitoProfile`` (detached from the session)."""

    id: int
    slug: str
    account_id: int | None


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


class AvitoDownloadError(Exception):
    pass


def _largest_image_url(content: dict) -> str | None:
    """URL of the biggest entry in ``content.image.sizes`` ({"640x480": url, ...})."""
    sizes = (content.get("image") or {}).get("sizes") or {}
    best: tuple[int, str] | None = None
    for key, url in sizes.items():
        if not isinstance(url, str) or not url:
            continue
        try:
            w, h = (int(part) for part in str(key).lower().split("x", 1))
            area = w * h
        except ValueError:
            area = 0
        if best is None or area > best[0]:
            best = (area, url)
    return best[1] if best else None


async def download_avito_image(url: str) -> bytes:
    """GET an image from Avito's CDN: https only, 30 s, at most 10 MB."""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or not (host in {"avito.st", "avito.ru"} or host.endswith((".avito.st", ".avito.ru")))):
            raise ValueError
    except ValueError:
        raise AvitoDownloadError("недопустимый адрес") from None

    class PublicResolver(aiohttp.resolver.DefaultResolver):
        async def resolve(self, host, port=0, family=0):
            records = await super().resolve(host, port, family)
            try:
                if not records or any(not ipaddress.ip_address(r["host"]).is_global for r in records):
                    raise AvitoDownloadError("недопустимый IP-адрес")
            except ValueError:
                raise AvitoDownloadError("недопустимый IP-адрес") from None
            return records

    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(resolver=PublicResolver())
    async with aiohttp.ClientSession(timeout=timeout, connector=connector, trust_env=False) as http:
        async with http.get(url, allow_redirects=False) as response:
            if response.status == 413:
                raise AvitoDownloadError("файл слишком большой")
            if response.status != 200:
                raise AvitoDownloadError(f"HTTP {response.status}")
            if response.content_length and response.content_length > AVITO_IMAGE_LIMIT:
                raise AvitoDownloadError("файл больше 10 МБ")
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data += chunk
                if len(data) > AVITO_IMAGE_LIMIT:
                    raise AvitoDownloadError("файл больше 10 МБ")
    return bytes(data)


async def _avito_image(value: dict) -> tuple[str, list[StoredFile]]:
    """Keep the provider URL, fetch and validate its content only when requested."""
    url = _largest_image_url(value.get("content") or {})
    if url is None:
        return "[вложение: image, не загружено: нет ссылки]", []
    return "", [StoredFile(kind="image", mime="image/jpeg", original_name="avito_image.jpg",
                           size=0, sha256="", path="", remote_provider="avito_image", remote_id=url)]


async def _avito_voice(value: dict, profile_id: int) -> tuple[str, list[StoredFile]]:
    voice = ((value.get("content") or {}).get("voice") or {}).get("voice_id")
    if not isinstance(voice, str) or not voice:
        return "[вложение: voice, не загружено: нет идентификатора]", []
    return "", [StoredFile(kind="voice", mime="audio/mp4", original_name="voice.mp4", size=0,
                           sha256="", path="", remote_provider="avito_voice", remote_id=voice,
                           remote_profile_id=profile_id)]


async def process_avito_message(value: dict, profile: ProfileRef) -> None:
    chat_id = str(value.get("chat_id") or "")
    author_id = value.get("author_id") or value.get("user_id")
    media_type = (value.get("content") or {}).get("type") or value.get("type")
    is_image = media_type == "image"
    is_voice = media_type == "voice"
    body = "" if is_image or is_voice else _extract_text(value)
    if not chat_id or author_id is None or (not body and not is_image and not is_voice):
        return
    try:
        author_id = int(author_id)
    except (TypeError, ValueError):
        logger.warning("Ignoring Avito message with invalid author_id: %r", author_id)
        return
    if profile.account_id is not None and author_id == profile.account_id:
        return  # our own message echoed back
    files: list[StoredFile] = []
    if is_image:
        body, files = await _avito_image(value)
    elif is_voice:
        body, files = await _avito_voice(value, profile.id)

    async with session_factory() as session:
        user = await get_or_create_avito_user(session, author_id, chat_id)
        chat = await session.scalar(
            select(AvitoChat).where(AvitoChat.profile_id == profile.id, AvitoChat.chat_id == chat_id)
        )
        if chat is None:
            chat = AvitoChat(profile_id=profile.id, chat_id=chat_id, user_id=user.id)
            session.add(chat)
        chat.last_message_at = datetime.now(timezone.utc)
        await session.flush()
        if user.full_name.startswith("Avito "):
            name = await _chat_user_name(session, profile.id, chat_id)
            if name:
                user.full_name = name
        if user.is_banned:
            await session.commit()
            discard(files)
            return
        event = await queue_support_message(
            session,
            user,
            body,
            order_id=user.current_order_id,
            external_id=f"avito:{profile.id}:{value.get('id')}",
            attachments=files,
            avito_chat_ref=chat.id,
        )
        await session.commit()
        if event is None:
            discard(files)


async def _chat_user_name(session, profile_id: int, chat_id: str) -> str | None:
    profile = await session.get(AvitoProfile, profile_id)
    if profile is None:
        return None
    try:
        return await client_for_profile(profile).get_chat_user_name(chat_id)
    except AvitoError:
        return None


async def find_profile(secret: str) -> ProfileRef | None:
    """Enabled profile whose webhook secret hashes to ``sha256(secret)``."""
    if not secret:
        return None
    async with session_factory() as session:
        profile = await session.scalar(
            select(AvitoProfile).where(AvitoProfile.webhook_secret_hash == webhook_hash(secret))
        )
        if profile is None or not profile.enabled:
            return None
        return ProfileRef(profile.id, profile.slug, profile.account_id)


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def handle_webhook(request: web.Request) -> web.Response:
    profile = await find_profile(request.match_info.get("secret", ""))
    if profile is None:
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
            await process_avito_message(value, profile)
        except Exception:
            logger.exception("Could not persist Avito webhook %s (profile %s)", value.get("id"), profile.slug)
            return web.json_response({"error": "temporary storage error"}, status=503)
        return web.json_response({"ok": True})


def create_web_application() -> web.Application:
    app = web.Application(client_max_size=1_048_576, middlewares=[rate_limit])
    app.router.add_get("/health", health)
    # Profiles are added from the site at any time, so the route always exists;
    # an unknown or disabled profile secret answers 404.
    app.router.add_post(WEBHOOK_ROUTE, handle_webhook)
    if robokassa_ready():
        app.router.add_get("/robokassa/result", handle_robokassa_result)
        app.router.add_post("/robokassa/result", handle_robokassa_result)
    return app


async def start_web_server() -> web.AppRunner:
    settings = get_settings()
    runner = web.AppRunner(create_web_application())
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.avito_webhook_port)
    await site.start()
    logger.info("Webhook server listening on port %s", settings.avito_webhook_port)
    return runner
