from __future__ import annotations

import shlex

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from support_bot import repositories as repo
from support_bot.keyboards import order_status_actions
from support_bot.models import BotUser, SupportRequest
from support_bot.source_repository import (
    FULFILLMENT_STATUSES,
    change_order_status,
    create_order,
    decide_payment,
    set_client_banned,
)
from support_bot.states import RequestStatus

router = Router(name="orders")


async def _command_request(session: AsyncSession, user: BotUser) -> SupportRequest | None:
    staff = await repo.get_active_staff_member(session, user)
    if staff is None:
        return None
    request = await repo.get_request(session, user.active_request_id or 0)
    if request is None or request.source != "main" or request.status == RequestStatus.CLOSED.value:
        return None
    if not repo.is_admin(user.id) and request.assigned_to != user.id:
        return None
    return request


async def _order_request(
    session: AsyncSession, user: BotUser, order_id: int
) -> SupportRequest | None:
    staff = await repo.get_active_staff_member(session, user)
    if staff is None:
        return None
    request = await session.scalar(
        select(SupportRequest)
        .where(
            SupportRequest.source == "main",
            SupportRequest.source_order_id == order_id,
            SupportRequest.status != RequestStatus.CLOSED.value,
        )
        .order_by(SupportRequest.id.desc())
        .limit(1)
    )
    if request is None:
        return None
    if not repo.is_admin(user.id) and request.assigned_to != user.id:
        return None
    return request


@router.callback_query(F.data.startswith("order:confirm:"))
async def confirm(call: CallbackQuery, session: AsyncSession, user: BotUser) -> None:
    await _decide(call, session, user, True)


@router.callback_query(F.data.startswith("order:reject:"))
async def reject(call: CallbackQuery, session: AsyncSession, user: BotUser) -> None:
    await _decide(call, session, user, False)


async def _decide(call: CallbackQuery, session: AsyncSession, user: BotUser, approved: bool) -> None:
    order_id = int(call.data.split(":")[2])
    request = await _order_request(session, user, order_id)
    if request is None:
        await call.answer("Сначала возьмите обращение в работу", show_alert=True)
        return
    order, error = await decide_payment(order_id, approved)
    if error is not None:
        await call.answer(error, show_alert=True)
        return
    decision = "оплата подтверждена" if approved else "оплата отклонена"
    await repo.add_message(session, request, user.id, "system", f"Заказ #{order_id}: {decision}")
    if call.message is not None:
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        if approved:
            await call.message.answer(
                f"Заказ #{order_id}: выберите статус",
                reply_markup=order_status_actions(order_id, list(FULFILLMENT_STATUSES)),
            )
    await call.answer(decision)


@router.callback_query(F.data.startswith("order:status:"))
async def status(call: CallbackQuery, session: AsyncSession, user: BotUser) -> None:
    _, _, order_id_raw, status_value = call.data.split(":")
    order_id = int(order_id_raw)
    request = await _order_request(session, user, order_id)
    if request is None:
        await call.answer("Недостаточно прав для этого заказа", show_alert=True)
        return
    order, error = await change_order_status(order_id, status_value)
    if error is not None:
        await call.answer(error, show_alert=True)
        return
    label = dict(FULFILLMENT_STATUSES)[status_value]
    await repo.add_message(
        session, request, user.id, "system", f"Заказ #{order_id}: статус — {label}"
    )
    if call.message is not None:
        try:
            await call.message.edit_text(f"Заказ #{order_id}: текущий статус — {label}")
        except TelegramBadRequest:
            pass
    await call.answer(f"Статус: {label}")


def _parse_order_args(raw: str) -> tuple[str, str, int] | None:
    raw = raw.strip()
    if "|" in raw:
        parts = [part.strip() for part in raw.split("|")]
        if not parts[-1].isdigit():
            return None
        price = int(parts[-1])
        title = parts[0]
        description = " | ".join(parts[1:-1]) if len(parts) > 2 else ""
    else:
        try:
            tokens = shlex.split(raw)
        except ValueError:
            tokens = raw.split()
        if len(tokens) < 2 or not tokens[-1].isdigit():
            return None
        price = int(tokens[-1])
        title = tokens[0] if len(tokens) == 2 else " ".join(tokens[:-1])
        description = ""
        if any(quote in raw for quote in ('"', "'", "“", "”")) and len(tokens) > 2:
            description = " ".join(tokens[1:-1])
    if not title or not 1 <= price <= 1_000_000_000:
        return None
    return title, description, price


@router.message(Command("order"), F.chat.type == "private")
async def order_command(
    message: Message, command: CommandObject, session: AsyncSession, user: BotUser
) -> None:
    request = await _command_request(session, user)
    if request is None:
        await message.answer("Откройте назначенное вам обращение из основного бота.")
        return
    parsed = _parse_order_args(command.args or "")
    if parsed is None:
        await message.answer('Использование: /order "Название" [описание] сумма')
        return
    title, description, price = parsed
    if len(title) > 256 or len(description) > 4000:
        await message.answer("Название — до 256 символов, описание — до 4000.")
        return
    order = await create_order(request, title, description, price)
    request.source_order_id = order.id
    await repo.add_message(
        session, request, user.id, "system", f"Выставлен счёт по заказу #{order.id} на {price} ₽"
    )
    await message.answer(f"Счёт по заказу #{order.id} отправлен клиенту.")


@router.message(Command("ban"), F.chat.type == "private")
async def ban_command(message: Message, session: AsyncSession, user: BotUser) -> None:
    await _ban(message, session, user, True)


@router.message(Command("pardon"), F.chat.type == "private")
async def pardon_command(message: Message, session: AsyncSession, user: BotUser) -> None:
    await _ban(message, session, user, False)


async def _ban(message: Message, session: AsyncSession, user: BotUser, banned: bool) -> None:
    request = await _command_request(session, user)
    if request is None:
        await message.answer("Откройте назначенное вам обращение из основного бота.")
        return
    await set_client_banned(request, banned)
    await repo.add_message(
        session,
        request,
        user.id,
        "system",
        "Клиент заблокирован" if banned else "Клиент разблокирован",
    )
    await message.answer("Клиент заблокирован" if banned else "Клиент разблокирован")
