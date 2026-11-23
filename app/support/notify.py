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
_last_sent: dict[tuple[int, int, str], float] = {}


def _due(thread_id: int, staff_id: int, kind: str, at: float) -> bool:
    if kind == "new_client":
        return True
    key = (thread_id, staff_id, kind)
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
    client = html.escape(n.client_name)
    if n.kind == "new_client":
        return (
            f"👋 <b>Новый потенциальный клиент в боте</b>\n"
            f"👤 <b>{client}</b>{cover}\n"
            f"💬 Ветка #{n.thread_id}"
        )
    if not n.assigned:
        return (
            f"❗️❗️❗️ <b>Срочно, новая заявка, бегите принимать!</b>\n"
            f"💬 <b>Ветка #{n.thread_id}</b> · {client}{cover}\n"
            f"{html.escape(snippet)}"
        )
    return (
        f"❗️❗️ <b>У вас новое сообщение, перейдите в диалог</b>\n"
        f"💬 <b>Ветка #{n.thread_id}</b> · {client}{cover}\n"
        f"{html.escape(snippet)}"
    )


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
                    inline_keyboard=[[InlineKeyboardButton(text="Открыть диалог", url=f"{base}/support/{n.thread_id}")]]
                )
            text = render(n, (cover_slugs or {}).get(n.cover_id) if n.cover_id else None)
            throttle_kind = "new_client" if n.kind == "new_client" else ("assigned" if n.assigned else "unassigned")
            for staff_id in n.recipients:
                if not _due(n.thread_id, staff_id, throttle_kind, time.monotonic()):
                    continue
                try:
                    await bot.send_message(staff_id, text, parse_mode="HTML", reply_markup=markup)
                except Exception as exc:  # noqa: BLE001 - e.g. the employee never started the bot
                    logger.warning("Support notification to %s failed: %s", staff_id, exc)
    finally:
        await bot.session.close()


async def send_staff_messages(messages: list[tuple[int, str] | tuple[int, str, str]]) -> None:
    """Plain service messages to employees (e.g. transfer or shift notice); optional 3rd item is a button URL."""
    token = login_bot_token()
    if not messages or not token:
        return
    base = get_settings().web_public_url.rstrip("/")
    bot = Bot(token)
    try:
        for item in messages:
            staff_id, text = item[0], item[1]
            path = item[2] if len(item) > 2 else None
            markup = None
            if path and base:
                url = path if path.startswith("http") else f"{base}{path}"
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="Открыть диалог", url=url)]]
                )
            try:
                await bot.send_message(staff_id, text, parse_mode="HTML", reply_markup=markup)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Staff message to %s failed: %s", staff_id, exc)
    finally:
        await bot.session.close()
