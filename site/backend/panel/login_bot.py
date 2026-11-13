"""Login bot: invite deep links (``?start=inv_<token>``) and site sign-in links (``?start=login_<token>``)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from fastapi import HTTPException

from app.config import get_settings
from app.login_bot_config import token as login_bot_token
from app.db import base as db_base
from app.db.models import Staff
from panel.auth import BOT_LOGIN_PREFIX, MAGIC_TTL_SEC, bot_login_pending, confirm_bot_login, issue_magic_link
from panel.routes.invites import consume_invite

logger = logging.getLogger(__name__)
router = Router()


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    arg = (message.text or "").partition(" ")[2].strip()
    if arg.startswith(BOT_LOGIN_PREFIX) and message.from_user is not None:
        await _ask_login_confirmation(message, arg[len(BOT_LOGIN_PREFIX):])
        return
    if not arg.startswith("inv_") or message.from_user is None:
        await _send_panel_link(message)
        return
    u = message.from_user
    try:
        async with db_base.session_factory() as session:
            staff = await consume_invite(
                session, arg[4:], u.id, u.full_name, u.username, None
            )
    except HTTPException as exc:
        await message.answer(f"⚠️ {exc.detail}")
        return
    except Exception:  # noqa: BLE001
        logger.exception("invite accept failed")
        await message.answer("Не удалось принять приглашение, попробуйте позже.")
        return
    markup = _open_panel_kb(staff.telegram_id)
    await message.answer(
        f"✅ Готово, {staff.display_name}! Вы добавлены в панель Тайби.\n"
        + ("Нажмите кнопку ниже — панель откроется, вход уже выполнен." if markup
           else "Теперь войдите на сайте через Telegram."),
        reply_markup=markup,
    )


def _open_panel_kb(staff_id: int) -> InlineKeyboardMarkup | None:
    url = issue_magic_link(staff_id)
    if url is None:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть панель", url=url)]])


async def _send_panel_link(message: Message) -> None:
    """Plain /start: an active employee gets a one-time sign-in button, others a hint."""
    staff = None
    if message.from_user is not None:
        try:
            async with db_base.session_factory() as session:
                staff = await session.get(Staff, message.from_user.id)
        except Exception:  # noqa: BLE001
            logger.exception("staff lookup failed")
    markup = _open_panel_kb(staff.telegram_id) if staff is not None and staff.is_active else None
    if markup is None:
        await message.answer(
            "Это бот входа в панель Тайби. Нажмите «Войти через Telegram-бота» на странице входа "
            "или откройте ссылку-приглашение от руководителя."
        )
        return
    await message.answer(
        f"👋 {staff.display_name}, кнопка ниже откроет панель сразу с входом.\n"
        f"Ссылка одноразовая и действует {MAGIC_TTL_SEC // 60} минут — никому её не пересылайте.",
        reply_markup=markup,
    )


async def _ask_login_confirmation(message: Message, token: str) -> None:
    if not bot_login_pending(token):
        await message.answer("⚠️ Ссылка для входа устарела. Обновите страницу входа на сайте и попробуйте снова.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить вход", callback_data=f"weblogin:ok:{token}"),
        InlineKeyboardButton(text="✖️ Отмена", callback_data="weblogin:no"),
    ]])
    await message.answer(
        "🔐 Вход в панель Тайби.\n\nПодтвердите, если это вы только что нажали «Войти» на сайте. "
        "Если вы не входили — нажмите «Отмена» и никому не пересылайте эту ссылку.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "weblogin:no")
async def on_login_cancel(cb: CallbackQuery) -> None:
    await cb.answer()
    if isinstance(cb.message, Message):
        await cb.message.edit_text("Вход отменён.")


@router.callback_query(F.data.startswith("weblogin:ok:"))
async def on_login_confirm(cb: CallbackQuery) -> None:
    token = (cb.data or "").removeprefix("weblogin:ok:")
    u = cb.from_user
    try:
        async with db_base.session_factory() as session:
            staff = await confirm_bot_login(session, token, u.id, u.full_name, u.username)
    except HTTPException as exc:
        await cb.answer()
        if isinstance(cb.message, Message):
            await cb.message.edit_text(f"⚠️ {exc.detail}")
        return
    except Exception:  # noqa: BLE001
        logger.exception("bot login confirm failed")
        await cb.answer("Не удалось подтвердить вход, попробуйте позже.", show_alert=True)
        return
    await cb.answer("Готово")
    if isinstance(cb.message, Message):
        await cb.message.edit_text(f"✅ Вход подтверждён, {staff.display_name}. Вернитесь на сайт — он откроется сам.")


async def run_login_bot() -> None:
    current = None
    task = None
    try:
        while True:
            token = login_bot_token()
            if token != current or (task is not None and task.done()):
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                current = token
                task = asyncio.create_task(_poll(token)) if token else None
            await asyncio.sleep(3)
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _poll(token: str) -> None:
    bot = Bot(token)
    dp = Dispatcher()
    fresh_router = Router()
    fresh_router.message.register(on_start, CommandStart())
    fresh_router.callback_query.register(on_login_cancel, F.data == "weblogin:no")
    fresh_router.callback_query.register(on_login_confirm, F.data.startswith("weblogin:ok:"))
    dp.include_router(fresh_router)
    try:
        await dp.start_polling(bot, handle_signals=False)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("login bot stopped")
    finally:
        await bot.session.close()
