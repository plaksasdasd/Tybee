from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.db.models import User
from app.keyboards import inline
from app.states import UserState

logger = logging.getLogger(__name__)
router = Router(name="broadcast")

_pending: dict[int, tuple[int, int]] = {}
_broadcast_task: asyncio.Task[None] | None = None


@router.callback_query(F.data == "adm:broadcast")
async def start(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        await call.answer(texts.ADMIN_ONLY, show_alert=True)
        return
    await repo.set_state(session, user, UserState.ADMIN_BROADCAST)
    if call.message is not None:
        await call.message.edit_text(
            texts.ADMIN_BROADCAST_PROMPT,
            reply_markup=inline.admin_add_cancel(),
        )
    await call.answer()


@router.callback_query(F.data == "adm:broadcast:cancel")
async def cancel(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        await call.answer(texts.ADMIN_ONLY, show_alert=True)
        return
    _pending.pop(user.id, None)
    await repo.set_state(session, user, UserState.MENU)
    if call.message is not None:
        try:
            await call.message.edit_text(texts.ADMIN_PANEL, reply_markup=inline.admin_panel())
        except TelegramBadRequest:
            pass
    await call.answer("Рассылка отменена")


async def process_broadcast(
    message: Message, bot: Bot, session: AsyncSession, user: User
) -> None:
    if not repo.is_admin(user):
        return
    _pending[user.id] = (message.chat.id, message.message_id)
    await repo.set_state(session, user, UserState.MENU)
    await bot.copy_message(user.id, message.chat.id, message.message_id)
    await bot.send_message(
        user.id,
        texts.ADMIN_BROADCAST_CONFIRM,
        reply_markup=inline.admin_broadcast_confirm(),
    )


@router.callback_query(F.data == "adm:broadcast:send")
async def send(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    global _broadcast_task
    if not repo.is_admin(user):
        await call.answer(texts.ADMIN_ONLY, show_alert=True)
        return
    if _broadcast_task is not None and not _broadcast_task.done():
        await call.answer(texts.ADMIN_BROADCAST_RUNNING, show_alert=True)
        return
    pending = _pending.pop(user.id, None)
    if pending is None:
        await call.answer("Сообщение для рассылки не найдено", show_alert=True)
        return
    recipients = await repo.list_broadcast_user_ids(session)
    if not recipients:
        await call.answer(texts.ADMIN_BROADCAST_EMPTY, show_alert=True)
        return
    source_chat_id, message_id = pending
    _broadcast_task = asyncio.create_task(
        _run_broadcast(bot, user.id, source_chat_id, message_id, recipients)
    )
    if call.message is not None:
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await call.message.answer(texts.ADMIN_BROADCAST_STARTED.format(total=len(recipients)))
    await call.answer("Рассылка запущена")


async def _run_broadcast(
    bot: Bot,
    admin_id: int,
    source_chat_id: int,
    message_id: int,
    recipients: list[int],
) -> None:
    global _broadcast_task
    sent = 0
    blocked = 0
    failed = 0
    try:
        for recipient in recipients:
            try:
                await bot.copy_message(recipient, source_chat_id, message_id)
                sent += 1
            except TelegramForbiddenError:
                blocked += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)
        await bot.send_message(
            admin_id,
            texts.ADMIN_BROADCAST_DONE.format(sent=sent, blocked=blocked, failed=failed),
            parse_mode="HTML",
        )
        logger.info(
            "Broadcast by %s finished: sent=%s blocked=%s failed=%s",
            admin_id,
            sent,
            blocked,
            failed,
        )
    finally:
        _broadcast_task = None
