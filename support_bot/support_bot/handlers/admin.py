from __future__ import annotations

import html

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from support_bot import repositories as repo
from support_bot.keyboards import admin_panel, cancel_to_menu
from support_bot.models import BotUser
from support_bot.states import UserState

router = Router(name="admin")


async def _panel(bot: Bot, session: AsyncSession, user: BotUser) -> None:
    staff = await repo.list_staff(session)
    text = (
        "<b>Сотрудники поддержки</b>\n\n"
        "Нажмите на сотрудника, чтобы включить или отключить его доступ. "
        "Отключённые сотрудники не получают новые обращения и не могут брать их в работу."
    )
    await bot.send_message(user.id, text, parse_mode="HTML", reply_markup=admin_panel(staff))


@router.message(Command("admin"), F.chat.type == "private")
async def cmd_admin(message: Message, bot: Bot, session: AsyncSession, user: BotUser) -> None:
    if not repo.is_admin(user.id):
        await message.answer("Команда доступна только администратору.")
        return
    user.state = UserState.MENU.value
    user.active_request_id = None
    await _panel(bot, session, user)


@router.callback_query(F.data == "admin:open")
async def admin_open(call: CallbackQuery, bot: Bot, session: AsyncSession, user: BotUser) -> None:
    if not repo.is_admin(user.id):
        await call.answer("Недостаточно прав", show_alert=True)
        return
    await _panel(bot, session, user)
    await call.answer()


@router.callback_query(F.data == "admin:add")
async def admin_add(call: CallbackQuery, session: AsyncSession, user: BotUser) -> None:
    if not repo.is_admin(user.id):
        await call.answer("Недостаточно прав", show_alert=True)
        return
    user.state = UserState.ADMIN_ADD_STAFF.value
    user.active_request_id = None
    if call.message is not None:
        await call.message.answer(
            "Отправьте: <code>Telegram_ID имя сотрудника</code>\n"
            "Например: <code>123456789 Мария</code>",
            parse_mode="HTML",
            reply_markup=cancel_to_menu(),
        )
    await call.answer()


@router.callback_query(F.data.startswith("admin:toggle:"))
async def admin_toggle(call: CallbackQuery, bot: Bot, session: AsyncSession, user: BotUser) -> None:
    if not repo.is_admin(user.id):
        await call.answer("Недостаточно прав", show_alert=True)
        return
    telegram_id = int(call.data.split(":")[2])
    if repo.is_admin(telegram_id):
        await call.answer("Администратора из SUPPORT_ADMIN_IDS нельзя отключить", show_alert=True)
        return
    staff = await repo.get_staff(session, telegram_id)
    if staff is None:
        await call.answer("Сотрудник не найден", show_alert=True)
        return
    staff.is_active = not staff.is_active
    await _panel(bot, session, user)
    await call.answer("Доступ включён" if staff.is_active else "Доступ отключён")


async def process_add_staff(bot: Bot, message: Message, session: AsyncSession, user: BotUser) -> None:
    if not repo.is_admin(user.id):
        user.state = UserState.MENU.value
        await message.answer("Недостаточно прав.")
        return
    raw = (message.text or "").strip()
    telegram_id_raw, _, name = raw.partition(" ")
    name = name.strip()
    if not telegram_id_raw.isdigit() or int(telegram_id_raw) <= 0 or not 1 <= len(name) <= 80:
        await message.answer(
            "Нужно: <code>Telegram_ID имя сотрудника</code>. Имя — до 80 символов.",
            parse_mode="HTML",
            reply_markup=cancel_to_menu(),
        )
        return
    telegram_id = int(telegram_id_raw)
    staff = await repo.save_staff(session, telegram_id, name)
    user.state = UserState.MENU.value
    await message.answer(
        f"Сотрудник сохранён: <b>{html.escape(staff.display_name)}</b> "
        f"(<code>{staff.telegram_id}</code>)",
        parse_mode="HTML",
    )
    await _panel(bot, session, user)
