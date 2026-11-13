"""Encryption of Avito client secrets at rest (Fernet, key from AVITO_SECRET_KEY)."""
from __future__ import annotations

import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class AvitoKeyError(RuntimeError):
    """AVITO_SECRET_KEY is missing/invalid or a stored secret cannot be decrypted."""


def _fernet() -> Fernet:
    key = get_settings().avito_secret_key.strip()
    if not key:
        raise AvitoKeyError("AVITO_SECRET_KEY не задан: профили Avito нельзя создавать и использовать")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise AvitoKeyError("AVITO_SECRET_KEY неверный: нужен ключ Fernet (32 байта в urlsafe base64)") from exc


def key_configured() -> bool:
    try:
        _fernet()
    except AvitoKeyError:
        return False
    return True


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise AvitoKeyError("не удалось расшифровать client_secret (сменился AVITO_SECRET_KEY?)") from exc


def webhook_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()
