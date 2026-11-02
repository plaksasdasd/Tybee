from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlencode

from app.config import get_settings
from app.db.models import Order

_PAYMENT_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"
_ALGORITHMS = {
    "MD5": "md5",
    "SHA1": "sha1",
    "SHA256": "sha256",
    "SHA512": "sha512",
}


def robokassa_ready() -> bool:
    settings = get_settings()
    return bool(
        settings.robokassa_shop_id.strip()
        and settings.robokassa_password1
        and settings.robokassa_password2
    )


def _hash_name() -> str:
    normalized = get_settings().robokassa_hash_algorithm.upper().replace("-", "")
    try:
        return _ALGORITHMS[normalized]
    except KeyError as exc:
        raise RuntimeError(f"Unsupported Robokassa hash algorithm: {normalized}") from exc


def _signature(value: str) -> str:
    return hashlib.new(_hash_name(), value.encode("utf-8")).hexdigest().upper()


def payment_link(order: Order) -> str | None:
    if not robokassa_ready():
        return None
    settings = get_settings()
    amount = f"{order.price:.2f}"
    invoice_id = str(order.id)
    description = (order.title or f"Заказ #{order.id}")[:100]
    signature = _signature(
        f"{settings.robokassa_shop_id}:{amount}:{invoice_id}:{settings.robokassa_password1}"
    )
    query = {
        "MerchantLogin": settings.robokassa_shop_id,
        "OutSum": amount,
        "InvId": invoice_id,
        "Description": description,
        "SignatureValue": signature,
        "Culture": "ru",
    }
    if settings.robokassa_test:
        query["IsTest"] = "1"
    return f"{_PAYMENT_URL}?{urlencode(query)}"


def valid_result_signature(out_sum: str, inv_id: str, signature: str) -> bool:
    expected = _signature(f"{out_sum}:{inv_id}:{get_settings().robokassa_password2}")
    return hmac.compare_digest(expected, signature.upper())
