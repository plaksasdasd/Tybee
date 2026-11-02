from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(validation_alias="TYBEE_BOT_TOKEN")
    admin_ids_raw: str = Field(default="", validation_alias="TYBEE_ADMIN_IDS")

    payment_requisites: str = ""
    unban_fine_rub: int = 50

    avito_enabled: bool = False
    avito_client_id: str = ""
    avito_client_secret: str = ""
    avito_webhook_url: str = ""
    avito_webhook_secret: str = ""
    avito_webhook_port: int = 8080

    robokassa_shop_id: str = ""
    robokassa_password1: str = ""
    robokassa_password2: str = ""
    robokassa_hash_algorithm: str = "MD5"
    robokassa_test: bool = True

    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_db: str = "taibi"
    postgres_user: str = "taibi"
    postgres_password: str = "taibi"

    @field_validator("avito_enabled", "robokassa_test", mode="before")
    @classmethod
    def _parse_optional_bool(cls, value: object) -> object:
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
        return self.avito_enabled and bool(self.avito_client_id and self.avito_client_secret)

    @property
    def avito_webhook_path(self) -> str:
        return f"/avito/webhook/{self.avito_webhook_secret.strip()}"

    @property
    def avito_subscription_url(self) -> str:
        return f"{self.avito_webhook_url.rstrip('/')}{self.avito_webhook_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
