from __future__ import annotations

from enum import Enum
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "banners"


class Banner(str, Enum):
    WELCOME = "welcome"
    PROFILE = "profile"
    ARTICLE = "article"
    SEARCH = "search"
    PAYMENT = "payment"
    ALMOST = "almost"
    APPROVED = "approved"
    REJECTED = "rejected"
    LOGO = "logo"

    @property
    def path(self) -> Path:
        return ASSETS_DIR / f"{self.value}.png"
