"""Telegram notifications to staff through the login bot."""
from __future__ import annotations

import html
import logging
import time

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import get_settings
from app.login_bot_config import token as login_bot_token
from app.support.ingest import Notification

logger = logging.getLogger(__name__)
ANTISPAM_SEC = 60
_last_sent: dict[tuple[int, int], float] = {}


def _due(thread_id: int, staff_id: int, at: float) -> bool:
    key = (thread_id, staff_id)
    if at - _last_sent.get(key, 0.0) < ANTISPAM_SEC:
        return False
    _last_sent[key] = at
    if len(_last_sent) > 5000:
        for k in [k for k, v in _last_sent.items() if at - v > ANTISPAM_SEC]:
            _last_sent.pop(k, None)
    return True


def render(n: Notification, cover_slug: str | None) -> str:
    snippet = n.text if len(n.text) <= 200 else n.text[:197] + "…"
    cover = f" · {html.escape(cover_slug)}" if cover_slug else ""
    return f"💬 <b>Ветка #{n.thread_id}</b> · {html.escape(n.client_name)}{cover}\n{html.escape(snippet)}"


async def send_notifications(notifications: list[Notification], cover_slugs: dict[int, str] | None = None) -> None:
    settings = get_settings()
    token = login_bot_token()
    if not notifications or not token:
        return
    base = settings.web_public_url.rstrip("/")
    bot = Bot(token)
    try:
        for n in notifications:
            markup = None
            if base:
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="Открыть", url=f"{base}/support/{n.thread_id}")]]
                )
            text = render(n, (cover_slugs or {}).get(n.cover_id) if n.cover_id else None)
            for staff_id in n.recipients:
                if not _due(n.thread_id, staff_id, time.monotonic()):
                    continue
                try:
                    await bot.send_message(staff_id, text, parse_mode="HTML", reply_markup=markup)
                except Exception as exc:  # noqa: BLE001 - e.g. the employee never started the bot
                    logger.warning("Support notification to %s failed: %s", staff_id, exc)
    finally:
        await bot.session.close()


async def send_staff_messages(messages: list[tuple[int, str]]) -> None:
    """Plain service messages to employees (e.g. "your shift was ended")."""
    settings = get_settings()
    token = login_bot_token()
    if not messages or not token:
        return
    bot = Bot(token)
    try:
        for staff_id, text in messages:
            try:
                await bot.send_message(staff_id, text, parse_mode="HTML")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Staff message to %s failed: %s", staff_id, exc)
    finally:
        await bot.session.close()
