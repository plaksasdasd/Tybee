from __future__ import annotations

import re

ARTICLE_RE = re.compile(r"^\d{6}$")
PRICE_MIN = 1
PRICE_MAX = 1_000_000_000
TITLE_MAX = 256
DESCRIPTION_MAX = 2000


class CatalogError(Exception):
    """A user-facing error (Russian message) with an HTTP-like status."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def check_price(price: int) -> int:
    if isinstance(price, bool) or not isinstance(price, int) or not PRICE_MIN <= price <= PRICE_MAX:
        raise CatalogError(f"Цена — целое число от {PRICE_MIN} до {PRICE_MAX:,}".replace(",", " "))
    return price


def check_title(title: str) -> str:
    title = (title or "").strip()
    if not title:
        raise CatalogError("Пустое название")
    if len(title) > TITLE_MAX:
        raise CatalogError(f"Название длиннее {TITLE_MAX} символов")
    return title
