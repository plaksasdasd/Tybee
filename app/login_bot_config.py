"""Shared, local configuration for the staff login bot."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app.config import get_settings


def token() -> str:
    path = Path(get_settings().data_dir) / "login_bot.json"
    try:
        return str(json.loads(path.read_text(encoding="utf-8"))["token"])
    except FileNotFoundError:
        return get_settings().web_login_bot_token


def save(value: str) -> None:
    directory = Path(get_settings().data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".login-bot-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"token": value}, f)
        os.replace(name, directory / "login_bot.json")
    finally:
        if os.path.exists(name):
            os.unlink(name)
