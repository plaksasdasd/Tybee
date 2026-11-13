from __future__ import annotations

import asyncio
import logging
import time

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import base as db_base
from app.db.models import AvitoProfile
from app.services.avito_secrets import decrypt_secret

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://api.avito.ru/token"
_API_BASE = "https://api.avito.ru"
_TOKEN_MARGIN = 300


class AvitoError(RuntimeError):
    pass


class AvitoProfileUnavailable(AvitoError):
    """The profile is missing, disabled or its secret cannot be decrypted (permanent for the outbox)."""


class AvitoClient:
    """API client of ONE Avito profile (see :func:`get_avito`)."""

    def __init__(self, client_id: str, client_secret: str, account_id: int | None = None) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None
        self._token_expires_at = 0.0
        self.account_id: int | None = account_id
        self.enabled = True
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._token = None
        self._token_expires_at = 0.0

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "TybeeBot/1.0"},
            )
        return self._session

    async def _ensure_token(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token
            session = await self._http()
            async with session.post(
                _TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            ) as response:
                data = await response.json(content_type=None)
                if response.status != 200 or "access_token" not in data:
                    raise AvitoError(f"Avito auth failed ({response.status}): {data}")
            self._token = data["access_token"]
            self._token_expires_at = (
                time.monotonic() + int(data.get("expires_in", 86400)) - _TOKEN_MARGIN
            )
            self.account_id = (data.get("meta") or {}).get("user_id", self.account_id)
            return self._token

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        token = await self._ensure_token()
        session = await self._http()
        async with session.request(
            method,
            f"{_API_BASE}{path}",
            headers={"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})},
            **kwargs,
        ) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                raise AvitoError(f"Avito API {method} {path} -> {response.status}: {data}")
            return data or {}

    async def send_message(self, chat_id: str, text: str) -> None:
        if not self.enabled:
            raise AvitoError("Avito integration is not configured")
        if self.account_id is None:
            await self._ensure_token()
        await self._request(
            "POST",
            f"/messenger/v1/accounts/{self.account_id}/chats/{chat_id}/messages/",
            json={"message": {"text": text}},
        )

    async def upload_image(self, data: bytes, filename: str, mime: str) -> str:
        """``uploadImages``: returns the Avito image_id to send in a chat."""
        if not self.enabled:
            raise AvitoError("Avito integration is not configured")
        if self.account_id is None:
            await self._ensure_token()
        form = aiohttp.FormData()
        form.add_field("uploadfile[]", data, filename=filename or "image", content_type=mime)
        result = await self._request(
            "POST", f"/messenger/v1/accounts/{self.account_id}/uploadImages", data=form
        )
        image_id = next(iter(result), None) if isinstance(result, dict) else None
        if not image_id:
            raise AvitoError("Avito uploadImages returned no image_id")
        return str(image_id)

    async def send_image(self, chat_id: str, image_id: str) -> dict:
        if not self.enabled:
            raise AvitoError("Avito integration is not configured")
        if self.account_id is None:
            await self._ensure_token()
        return await self._request(
            "POST",
            f"/messenger/v1/accounts/{self.account_id}/chats/{chat_id}/messages/image",
            json={"image_id": image_id},
        )

    async def voice_url(self, voice_id: str) -> str | None:
        if self.account_id is None:
            await self._ensure_token()
        result = await self._request("GET", f"/messenger/v1/accounts/{self.account_id}/getVoiceFiles",
                                     params=[("voice_ids", voice_id)])
        return (result.get("voices_urls") or {}).get(voice_id)

    async def get_chat_user_name(self, chat_id: str) -> str | None:
        if not self.enabled:
            return None
        try:
            if self.account_id is None:
                await self._ensure_token()
            data = await self._request(
                "GET", f"/messenger/v2/accounts/{self.account_id}/chats/{chat_id}/"
            )
            chat = data.get("chat") or data
            for user in chat.get("users") or []:
                if user.get("id") != self.account_id and user.get("name"):
                    return str(user["name"])
        except (AvitoError, aiohttp.ClientError) as exc:
            logger.warning("Could not fetch Avito chat %s info: %s", chat_id, exc)
        return None

    async def subscribe_webhook(self, url: str) -> None:
        await self._request("POST", "/messenger/v2/webhook", json={"subscription_url": url})

    async def get_account_self(self) -> dict:
        """``/core/v1/accounts/self`` (also proves the keys work)."""
        data = await self._request("GET", "/core/v1/accounts/self")
        if data.get("id") is not None:
            self.account_id = int(data["id"])
        return data


# profile_id -> (cache key, client). The key changes whenever keys/updated_at change.
_clients: dict[int, tuple[tuple, AvitoClient]] = {}


def _drop(profile_id: int) -> None:
    cached = _clients.pop(profile_id, None)
    if cached is not None:
        try:
            asyncio.get_running_loop().create_task(cached[1].close())
        except RuntimeError:
            pass


def client_for_profile(profile: AvitoProfile) -> AvitoClient:
    """Cached client of ``profile``; a disabled profile drops its cache and raises."""
    if not profile.enabled:
        _drop(profile.id)
        raise AvitoProfileUnavailable(f"профиль Avito {profile.slug} отключён")
    key = (profile.updated_at, profile.client_id, profile.client_secret_enc)
    cached = _clients.get(profile.id)
    if cached is not None and cached[0] == key:
        client = cached[1]
        if client.account_id is None and profile.account_id is not None:
            client.account_id = profile.account_id
        return client
    _drop(profile.id)
    try:
        secret = decrypt_secret(profile.client_secret_enc)
    except Exception as exc:  # noqa: BLE001 - AvitoKeyError: no/changed AVITO_SECRET_KEY
        raise AvitoProfileUnavailable(f"профиль Avito {profile.slug} недоступен: {exc}") from exc
    client = AvitoClient(profile.client_id, secret, account_id=profile.account_id)
    _clients[profile.id] = (key, client)
    return client


async def _load_profile(session: AsyncSession, profile_id: int | None) -> AvitoProfile:
    if profile_id is None:
        profile = await session.scalar(select(AvitoProfile).where(AvitoProfile.slug == "default"))
    else:
        profile = await session.get(AvitoProfile, profile_id)
    if profile is None:
        raise AvitoProfileUnavailable("профиль Avito не найден")
    return profile


async def get_avito(profile_id: int | None = None, session: AsyncSession | None = None) -> AvitoClient:
    """Client of an Avito profile; without ``profile_id`` the ``default`` profile (compatibility)."""
    if session is not None:
        return client_for_profile(await _load_profile(session, profile_id))
    async with db_base.session_factory() as own:
        return client_for_profile(await _load_profile(own, profile_id))


async def close_avito() -> None:
    for profile_id in list(_clients):
        _, client = _clients.pop(profile_id)
        await client.close()
