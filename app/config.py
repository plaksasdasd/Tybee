from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Used ONLY to create covers/default/config.yaml on first start; afterwards
    # every cover (including "default") takes its token from its config.yaml.
    bot_token: str = Field(default="", validation_alias="TYBEE_BOT_TOKEN")
    admin_ids_raw: str = Field(default="", validation_alias="TYBEE_ADMIN_IDS")

    unban_fine_rub: int = 50
    profile_orders_limit: int = Field(default=10, ge=1)
    support_shift_offline_minutes: int = Field(default=3, ge=1)
    support_shift_idle_minutes: int = Field(default=15, ge=1)
    support_shift_no_reply_minutes: int = Field(default=10, ge=1)

    # Multi-cover setup: every cover is a folder under covers_root with its own
    # config.yaml (token, texts, buttons, statuses) and banners/.
    covers_root: str = "./covers"
    # Cover whose bot is used for service work shared by all covers
    # (e.g. downloading legacy product photos).
    support_cover_slug: str = "default"
    covers_reload_sec: int = 10
    # Runtime data (product photos) kept on disk, mounted as a volume.
    data_dir: str = "./data"

    # Web panel (site/). The login bot is a separate bot used only for the
    # Telegram Login Widget; it does not need polling.
    web_port: int = 8000
    web_admin_ids_raw: str = Field(default="", validation_alias="WEB_ADMIN_IDS")
    web_login_bot_token: str = ""
    web_login_bot_username: str = ""
    web_session_secret: str = ""
    web_public_url: str = ""
    web_dev_login: bool = Field(default=False, validation_alias=AliasChoices("WEB_DEV_LOGIN", "DEV"))
    web_cookie_secure: bool = False

    # Legacy single-account Avito settings: used ONLY once, to create the
    # "default" Avito profile (ТЗ-2, C.2). Profiles are managed on the site.
    avito_enabled: bool = False
    avito_client_id: str = ""
    avito_client_secret: str = ""
    avito_webhook_url: str = ""
    avito_webhook_secret: str = ""
    avito_webhook_port: int = 8080
    # Fernet key (32 bytes, urlsafe base64) encrypting Avito client secrets in the DB.
    # Needed by both processes (bot: decrypt for API calls; site: encrypt on save).
    avito_secret_key: str = ""

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

    @field_validator(
        "avito_enabled", "robokassa_test", "web_dev_login", "web_cookie_secure", mode="before"
    )
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
    def web_admin_ids(self) -> list[int]:
        return [
            int(part)
            for part in self.web_admin_ids_raw.replace(";", ",").split(",")
            if part.strip()
        ]

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
