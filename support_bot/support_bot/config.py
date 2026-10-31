from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
_ENV_FILE = str(_ROOT_ENV) if _ROOT_ENV.is_file() else ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(validation_alias="SUPPORT_BOT_TOKEN")
    admin_ids_raw: str = Field(default="", validation_alias="SUPPORT_ADMIN_IDS")
    staff_ids_raw: str = Field(default="", validation_alias="SUPPORT_STAFF_IDS")

    postgres_host: str = Field(default="db", validation_alias="SUPPORT_POSTGRES_HOST")
    postgres_port: int = Field(default=5432, validation_alias="SUPPORT_POSTGRES_PORT")
    postgres_db: str = Field(default="tybee_support", validation_alias="SUPPORT_POSTGRES_DB")
    postgres_user: str = Field(default="taibi", validation_alias="SUPPORT_POSTGRES_USER")
    postgres_password: str = Field(default="taibi", validation_alias="SUPPORT_POSTGRES_PASSWORD")

    source_postgres_host: str = ""
    source_postgres_port: int = 5432
    source_postgres_db: str = "taibi"
    source_postgres_user: str = "taibi"
    source_postgres_password: str = "taibi"

    @staticmethod
    def _ids(raw: str) -> list[int]:
        return [int(part) for part in raw.replace(";", ",").split(",") if part.strip()]

    @property
    def admin_ids(self) -> list[int]:
        return self._ids(self.admin_ids_raw)

    @property
    def staff_ids(self) -> list[int]:
        return self._ids(self.staff_ids_raw)

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
