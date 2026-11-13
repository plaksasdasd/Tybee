from __future__ import annotations

import hashlib
import hmac
import secrets
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Order, RobokassaPayment, User

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


def build_payment_link(invoice_id: int, amount: int, description: str) -> str | None:
    if not robokassa_ready():
        return None
    settings = get_settings()
    out_sum = f"{amount:.2f}"
    invoice = str(invoice_id)
    signature = _signature(
        f"{settings.robokassa_shop_id}:{out_sum}:{invoice}:{settings.robokassa_password1}"
    )
    query = {
        "MerchantLogin": settings.robokassa_shop_id,
        "OutSum": out_sum,
        "InvId": invoice,
        "Description": description[:100],
        "SignatureValue": signature,
        "Culture": "ru",
    }
    if settings.robokassa_test:
        query["IsTest"] = "1"
    return f"{_PAYMENT_URL}?{urlencode(query)}"


def payment_link(order: Order) -> str | None:
    return build_payment_link(order.id, order.price, order.title or f"Заказ #{order.id}")


def valid_result_signature(out_sum: str, inv_id: str, signature: str) -> bool:
    expected = _signature(f"{out_sum}:{inv_id}:{get_settings().robokassa_password2}")
    return hmac.compare_digest(expected, signature.upper())


async def create_unban_payment(
    session: AsyncSession, user: User, *, cover_id: int | None = None, amount: int | None = None
) -> RobokassaPayment:
    existing = await session.scalar(
        select(RobokassaPayment)
        .where(
            RobokassaPayment.user_id == user.id,
            RobokassaPayment.status == "pending",
        )
        .order_by(RobokassaPayment.id.desc())
        .limit(1)
    )
    if existing is not None:
        if cover_id is not None:
            existing.cover_id = cover_id  # confirmation goes to the cover paid from
        return existing
    while True:
        invoice_id = secrets.randbelow(100_000_000) + 2_000_000_001
        if await session.get(RobokassaPayment, invoice_id) is not None:
            continue
        if await session.get(Order, invoice_id) is None:
            break
    payment = RobokassaPayment(
        id=invoice_id,
        user_id=user.id,
        amount=amount if amount is not None else get_settings().unban_fine_rub,
        cover_id=cover_id,
    )
    session.add(payment)
    await session.flush()
    return payment
