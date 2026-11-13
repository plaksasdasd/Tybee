"""Telegram notifications about money, sent through the login bot (best effort)."""
from __future__ import annotations

import html
import logging
from collections.abc import Iterable

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.login_bot_config import token as login_bot_token
from app.db.models import Staff, StaffRole

logger = logging.getLogger(__name__)
_APPROVER_ROLES = (StaffRole.FINANCE_MANAGER.value, StaffRole.ADMIN.value)


def fmt_rub(kop: int) -> str:
    sign = "-" if kop < 0 else ""
    rub, rest = divmod(abs(kop), 100)
    text = f"{rub:,}".replace(",", " ")
    return f"{sign}{text},{rest:02d} ₽" if rest else f"{sign}{text} ₽"


async def approvers(session: AsyncSession, exclude: int | None = None) -> list[int]:
    ids = await session.scalars(select(Staff.telegram_id).where(Staff.role.in_(_APPROVER_ROLES), Staff.is_active.is_(True)))
    return [i for i in ids if i != exclude]


async def send(staff_ids: Iterable[int], text: str, path: str = "/finance") -> None:
    """Never raises: a member who never opened the bot must not break a money operation."""
    ids = list(dict.fromkeys(staff_ids))
    settings = get_settings()
    token = login_bot_token()
    if not ids or not token:
        return
    bot = Bot(token)
    try:
        base = settings.web_public_url.rstrip("/")
        markup = (
            InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть", url=f"{base}{path}")]]) if base else None
        )
        for staff_id in ids:
            try:
                await bot.send_message(staff_id, text, parse_mode="HTML", reply_markup=markup)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Finance notification to %s failed: %s", staff_id, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Finance notification failed: %s", exc)
    finally:
        await bot.session.close()


def esc(value: str | None) -> str:
    return html.escape(value or "")
