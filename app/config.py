from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    # Stored as raw strings to avoid pydantic-settings JSON-decoding env values.
    admin_ids_raw: str = Field(default="", validation_alias="ADMIN_IDS")
    support_chat_id: int | None = None

    # Payment requisites shown to the client when an invoice is issued. If empty,
    # a sensible placeholder from texts is used.
    payment_requisites: str = ""
    # Fine (in Telegram Stars, XTR) a banned user must pay to get unblocked.
    unban_fine_stars: int = 50

    # Avito Messenger API integration. Master switch: the integration only
    # starts when AVITO_ENABLED=true AND the credentials below are filled in.
    avito_enabled: bool = False
    avito_client_id: str = ""
    avito_client_secret: str = ""
    # Public HTTPS base URL Avito can reach, e.g. https://bot.example.com.
    # The webhook is served at {avito_webhook_url}/avito/webhook/{secret}.
    avito_webhook_url: str = ""
    # Secret path segment protecting the webhook endpoint.
    avito_webhook_secret: str = ""
    # Local port the webhook HTTP server listens on.
    avito_webhook_port: int = 8080

    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_db: str = "taibi"
    postgres_user: str = "taibi"
    postgres_password: str = "taibi"

    @field_validator("support_chat_id", mode="before")
    @classmethod
    def _parse_support_chat_id(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("avito_enabled", mode="before")
    @classmethod
    def _parse_avito_enabled(cls, value: object) -> object:
        # An empty AVITO_ENABLED= in .env must mean "off", not a parse error.
        if isinstance(value, str) and not value.strip():
            return False
        return value

    @property
    def admin_ids(self) -> list[int]:
        return [
            int(part)
            for part in self.admin_ids_raw.replace(";", ",").split(",")
            if part.strip()
        ]

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def avito_ready(self) -> bool:
        """True when the Avito integration may start (toggle + credentials)."""
        return self.avito_enabled and bool(self.avito_client_id and self.avito_client_secret)

    @property
    def avito_webhook_path(self) -> str:
        secret = self.avito_webhook_secret.strip()
        return f"/avito/webhook/{secret}" if secret else "/avito/webhook"

    @property
    def avito_subscription_url(self) -> str:
        return f"{self.avito_webhook_url.rstrip('/')}{self.avito_webhook_path}"

    @property
    def support_target(self) -> int | None:
        if self.support_chat_id is not None:
            return self.support_chat_id
        admins = self.admin_ids
        return admins[0] if admins else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
