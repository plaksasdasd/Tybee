"""Avito Messenger API client.

Handles OAuth2 (client_credentials) token lifecycle with automatic refresh,
sending chat messages and webhook (un)subscription. A module-level singleton is
configured from settings; everything is a no-op unless AVITO_ENABLED=true and
the credentials are set, so the rest of the code can call it unconditionally.

API reference: https://www.avito.ru/developers/api-catalog/messenger/documentation
"""
from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from app.config import get_settings

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://api.avito.ru/token"
_API_BASE = "https://api.avito.ru"
# Refresh the token this many seconds before its actual expiry.
_TOKEN_MARGIN = 300


class AvitoError(RuntimeError):
    """Raised when the Avito API returns an unexpected response."""


class AvitoClient:
    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self.account_id: int | None = None
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        """Master switch + credentials, as configured in the environment."""
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
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200 or "access_token" not in data:
                    raise AvitoError(f"Avito auth failed ({resp.status}): {data}")
            self._token = data["access_token"]
            self._token_expires_at = time.monotonic() + int(data.get("expires_in", 86400)) - _TOKEN_MARGIN
            meta = data.get("meta") or {}
            self.account_id = meta.get("user_id", self.account_id)
            logger.info("Avito token acquired (account_id=%s)", self.account_id)
            return self._token

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        token = await self._ensure_token()
        session = await self._http()
        async with session.request(
            method,
            f"{_API_BASE}{path}",
            headers={"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})},
            **kwargs,
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                raise AvitoError(f"Avito API {method} {path} -> {resp.status}: {data}")
            return data or {}

    async def send_message(self, chat_id: str, text: str) -> None:
        """Send a plain-text message into an Avito chat."""
        if not self.enabled:
            raise AvitoError("Avito integration is not configured")
        account_id = self.account_id
        if account_id is None:
            # Force a token fetch to learn the account id.
            await self._ensure_token()
            account_id = self.account_id
        await self._request(
            "POST",
            f"/messenger/v1/accounts/{account_id}/chats/{chat_id}/messages/",
            json={"message": {"text": text}},
        )

    async def get_chat_user_name(self, chat_id: str) -> str | None:
        """Best-effort display name of the interlocutor in a chat."""
        if not self.enabled:
            return None
        try:
            account_id = self.account_id
            if account_id is None:
                await self._ensure_token()
                account_id = self.account_id
            data = await self._request(
                "GET", f"/messenger/v2/accounts/{account_id}/chats/{chat_id}/"
            )
            # Depending on the endpoint version the chat object is either the
            # response itself or nested under "chat".
            chat = data.get("chat") or data
            users = chat.get("users") or []
            for user in users:
                if user.get("id") != self.account_id and user.get("name"):
                    return str(user["name"])
        except (AvitoError, aiohttp.ClientError) as exc:
            logger.warning("Could not fetch Avito chat %s info: %s", chat_id, exc)
        return None

    async def subscribe_webhook(self, url: str) -> None:
        await self._request(
            "POST", "/messenger/v2/webhook", json={"subscription_url": url}
        )
        logger.info("Avito webhook subscribed: %s", url)

    async def unsubscribe_webhook(self, url: str) -> None:
        try:
            await self._request(
                "POST", "/messenger/v2/webhook/unsubscribe", json={"subscription_url": url}
            )
        except (AvitoError, aiohttp.ClientError) as exc:
            logger.warning("Avito webhook unsubscribe failed: %s", exc)


_client: AvitoClient | None = None


def get_avito() -> AvitoClient:
    """Module-level singleton bound to the settings (created lazily)."""
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
