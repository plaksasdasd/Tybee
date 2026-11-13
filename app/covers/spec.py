"""Reference structure of a cover's ``config.yaml``.

Single source of truth for the validator: which keys exist under ``texts``,
``buttons`` and ``statuses``, which ``str.format`` placeholders each string may
(and must) contain, and which texts are used as a banner caption (Telegram
limits a photo caption to 1024 characters). ``{brand}`` is always allowed.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.assets import Banner

TOP_LEVEL_KEYS = (
    "token",
    "brand_name",
    "unban_fine_rub",
    "texts",
    "buttons",
    "statuses",
)

CAPTION_LIMIT = 1024
BUTTON_LIMIT = 64
BANNER_MAX_BYTES = 10 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TOKEN_RE = r"^\d{6,12}:[A-Za-z0-9_-]{30,50}$"
SHARED = "shared"


@dataclass(frozen=True)
class KeySpec:
    # Placeholders the string must contain (and may not go beyond; {brand} is extra).
    placeholders: frozenset[str]
    # True when shown as a banner caption (<= 1024 chars).
    caption: bool = False


def _S(placeholders: tuple[str, ...] = (), caption: bool = False) -> KeySpec:
    return KeySpec(frozenset(placeholders), caption)


TEXTS = {
    "WELCOME": _S((), True),
    "PROFILE": _S(('name', 'orders_count', 'user_id'), True),
    "OFFER": _S((), True),
    "NO_ORDERS": _S((), True),
    "ORDERS_HEADER": _S((), True),
    "ORDER_CHOICE": _S((), True),
    "MYSTERY_LIST": _S((), True),
    "MYSTERY_EMPTY": _S((), True),
    "MYSTERY_OFFER": _S(('price', 'title'), True),
    "ARTICLE_ALREADY": _S((), True),
    "ARTICLE_PROMPT": _S((), True),
    "ARTICLE_INVALID": _S((), True),
    "REJECTED_CATALOG": _S(('code',), True),
    "FOUND": _S(('code', 'description', 'price', 'title'), True),
    "PAYMENT": _S(('order_id', 'price', 'title'), True),
    "PAYMENT_UNAVAILABLE": _S((), True),
    "APPROVED": _S(('order_id',), False),
    "REJECTED_PAYMENT": _S(('order_id',), False),
    "ORDER_CREATED": _S(('description', 'price', 'title'), False),
    "SUPPORT": _S((), True),
    "DIALOG_OPEN": _S((), True),
    "PERSONAL_PROMPT": _S((), True),
    "PERSONAL_DONE": _S(('order_id',), True),
    "BANNED_SCREEN": _S(('fine',), False),
    "UNBANNED": _S((), False),
    "OFFER_URL": _S((), False),
    "PROFILE_DEFAULT_NAME": _S((), False),
    "ORDER_DEFAULT_TITLE": _S((), False),
    "ORDER_PRICE_PART": _S(('price',), False),
    "ORDER_LINE": _S(('order_id', 'price_part', 'status', 'title'), False),
    "ANSWER_DIALOG_CLOSED": _S((), False),
    "ANSWER_ORDER_NOT_FOUND": _S((), False),
    "ANSWER_BOX_UNAVAILABLE": _S((), False),
    "ANSWER_BAN_LIFTED": _S((), False),
    "ANSWER_PAYMENT_UNAVAILABLE": _S((), False),
    "UNBAN_PAYMENT_MESSAGE": _S(('amount',), False),
    "UNBAN_PAYMENT_DESCRIPTION": _S(('user_id',), False),
    "ORDER_STATUS_PUSH": _S(('order_id', 'title'), False),
    "ORDER_PING": _S(('order_id', 'status', 'title'), False),
    "CMD_START_DESCRIPTION": _S((), False),
}

BUTTONS = {
    "BTN_NEW_ORDER": _S((), False),
    "BTN_PROFILE": _S((), False),
    "BTN_SUPPORT": _S((), False),
    "BTN_BACK": _S((), False),
    "BTN_MENU": _S((), False),
    "BTN_HOME": _S((), False),
    "BTN_OPEN_DIALOG": _S((), False),
    "BTN_CLOSE_DIALOG": _S((), False),
    "BTN_OFFER": _S((), False),
    "BTN_MY_ORDERS": _S((), False),
    "BTN_ORDER_ARTICLE": _S((), False),
    "BTN_ORDER_MYSTERY": _S((), False),
    "BTN_ORDER_PERSONAL": _S((), False),
    "BTN_PERSONAL_APPLICATION": _S((), False),
    "BTN_HAS_ARTICLE": _S((), False),
    "BTN_NO_ARTICLE": _S((), False),
    "BTN_PAY_ONLINE": _S((), False),
    "BTN_PAY_FINE": _S(('fine',), False),
    "BTN_TO_PAYMENT": _S((), False),
    "BTN_MYSTERY_ITEM": _S(('price', 'title'), False),
}

STATUSES = (
    "new",
    "awaiting_payment",
    "payment_review",
    "approved",
    "rejected",
    "in_dialog",
    "accepted",
    "bought",
    "shipping_to_ru",
    "customs",
    "shipping_in_ru",
    "awaiting_pickup",
    "received",
    "refund_pending",
    "refunded",
    "cancelled",
)


BANNERS = tuple(banner.value for banner in Banner)
