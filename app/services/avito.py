from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from app.config import get_settings

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://api.avito.ru/token"
_API_BASE = "https://api.avito.ru"
_TOKEN_MARGIN = 300


class AvitoError(RuntimeError):
    pass


class AvitoClient:
    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None
        self._token_expires_at = 0.0
        self.account_id: int | None = None
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return get_settings().avito_ready

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


_client: AvitoClient | None = None


def get_avito() -> AvitoClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = AvitoClient(settings.avito_client_id, settings.avito_client_secret)
    return _client


async def close_avito() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
