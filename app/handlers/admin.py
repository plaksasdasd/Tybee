from __future__ import annotations

import html
import shlex

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.config import get_settings
from app.db.models import OrderStatus, User
from app.keyboards import inline
from app.services import notify, support

router = Router(name="admin")

SUPPORT_TARGET = get_settings().support_target
SUPPORT_CHAT_ID = get_settings().support_chat_id

_IN_SUPPORT_OR_DM = (F.chat.id == SUPPORT_TARGET) | (F.chat.type == "private")


async def _resolve_target_user(session: AsyncSession, message: Message) -> User | None:
    if message.message_thread_id is not None:
        user = await repo.get_user_by_thread(session, message.message_thread_id)
        if user is not None:
            return user
    if message.reply_to_message is not None:
        user_id = await repo.resolve_relay(session, message.reply_to_message.message_id)
        if user_id is not None:
            return await repo.get_user(session, user_id)
    return None


async def _resolve_command_target(
    session: AsyncSession, message: Message, args: str | None
) -> tuple[User | None, str | None]:
    token = args.split()[0] if args else ""
    if token:
        if token.startswith("@") or not token.lstrip("-").isdigit():
            target = await repo.get_user_by_username(session, token)
            if target is None:
                return None, texts.TARGET_USERNAME_NOT_FOUND.format(
                    username=html.escape(token)
                )
            return target, None
        return await repo.get_or_create_user_by_id(session, int(token)), None

    target = await _resolve_target_user(session, message)
    return target, None


@router.callback_query(F.data.startswith("adm:confirm:"))
async def confirm(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    await _decide(call, bot, session, user, approve=True)


@router.callback_query(F.data.startswith("adm:reject:"))
async def reject(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    await _decide(call, bot, session, user, approve=False)


async def _decide(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, approve: bool
) -> None:
    try:
        staff = await support.require_staff(session, user.id)
        order = await support.decide_payment(
            bot,
            session,
            staff,
            int(call.data.split(":")[2]),
            approve,
            channel="telegram",
        )
    except support.SupportError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    if call.message is not None:
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
    await call.answer("оплата подтверждена ✅" if order.status == OrderStatus.APPROVED.value else "заявка отклонена ❎")


@router.callback_query(F.data.startswith("adm:status:"))
async def change_status(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    _, _, order_id_raw, status_raw = call.data.split(":")
    try:
        staff = await support.require_staff(session, user.id)
        status = OrderStatus(status_raw)
        order = await support.change_order_status(
            bot, session, staff, int(order_id_raw), status, channel="telegram"
        )
    except (ValueError, support.SupportError) as exc:
        await call.answer(str(exc) or "Неизвестный статус", show_alert=True)
        return

    if call.message is not None:
        try:
            await call.message.edit_text(
                texts.ORDER_PANEL.format(
                    order_id=order.id,
                    title=html.escape(order.title or "Заказ"),
                    status=notify.status_label(order.status),
                ),
                parse_mode="HTML",
                reply_markup=inline.order_status(order.id, order.status),
            )
        except TelegramBadRequest:
            pass
    await call.answer(f"Статус: {notify.status_label(order.status)}")


@router.message(Command("order"), F.chat.id == SUPPORT_TARGET)
async def cmd_order(
    message: Message, command: CommandObject, bot: Bot, session: AsyncSession, user: User
) -> None:
    try:
        staff = await support.require_staff(session, user.id)
    except support.SupportError:
        await message.reply(texts.CMD_NOT_ALLOWED)
        return
    if not command.args:
        await message.reply(texts.ORDER_USAGE, parse_mode="HTML")
        return

    raw = command.args.strip()
    title = ""
    description = ""
    price: int | None = None
    if "|" in raw:
        parts = [p.strip() for p in raw.split("|")]
        if parts[-1].isdigit():
            price = int(parts[-1])
            title = parts[0]
            description = " | ".join(parts[1:-1]) if len(parts) > 2 else ""
    else:
        try:
            tokens = shlex.split(raw)
        except ValueError:
            tokens = raw.split()
        if len(tokens) >= 2 and tokens[-1].isdigit():
            price = int(tokens[-1])
            if any(q in raw for q in ('"', "'", "“", "”", "‘", "’")):
                title = tokens[0]
                description = " ".join(tokens[1:-1])
            else:
                title = tokens[0] if len(tokens) == 2 else " ".join(tokens[:-1])

    target = await _resolve_target_user(session, message)
    if target is None or not title or price is None:
        await message.reply(
            texts.ORDER_NEED_THREAD if target is None else texts.ORDER_USAGE,
            parse_mode="HTML",
        )
        return
    try:
        order = await support.create_support_order(
            bot,
            session,
            staff,
            target.id,
            title,
            description,
            price,
            channel="telegram",
        )
    except support.SupportError as exc:
        await message.reply(str(exc))
        return
    await message.reply(f"Счёт по заказу #{order.id} отправлен клиенту.")


@router.message(Command("ban"), _IN_SUPPORT_OR_DM)
async def cmd_ban(
    message: Message, command: CommandObject, session: AsyncSession, user: User
) -> None:
    await _set_ban(message, command, session, user, True)


@router.message(Command("pardon"), _IN_SUPPORT_OR_DM)
async def cmd_pardon(
    message: Message, command: CommandObject, session: AsyncSession, user: User
) -> None:
    await _set_ban(message, command, session, user, False)


async def _set_ban(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    banned: bool,
) -> None:
    try:
        staff = await support.require_staff(session, user.id)
    except support.SupportError:
        await message.reply(texts.CMD_NOT_ALLOWED)
        return
    target, error = await _resolve_command_target(session, message, command.args)
    if error is not None:
        await message.reply(error, parse_mode="HTML")
        return
    if target is None:
        await message.reply(texts.BAN_USAGE if banned else texts.PARDON_USAGE, parse_mode="HTML")
        return
    if target.is_banned == banned:
        await message.reply(texts.ALREADY_BANNED if banned else texts.NOT_BANNED)
        return
    await support.set_client_banned(
        session, staff, target.id, banned, channel="telegram"
    )
    text = texts.BAN_DONE if banned else texts.PARDON_DONE
    await message.reply(text + f"\n{repo.display_name(target) or target.id}")


@router.message(F.chat.id == SUPPORT_CHAT_ID)
async def thread_message(message: Message, bot: Bot, session: AsyncSession, user: User) -> None:
    if message.from_user is None or message.from_user.is_bot:
        return
    if message.text and message.text.startswith("/"):
        return
    staff = await support.get_staff(session, user.id)
    if staff is None:
        return
    target = await _resolve_target_user(session, message)
    if target is None:
        return
    body = message.text or message.caption
    if body is None:
        await notify.relay_admin_to_user(bot, target, message)
        body = message.caption or "[вложение]"
        await repo.record_support_message(
            session,
            target.id,
            "outbound",
            body,
            "telegram",
            operator_telegram_id=staff.id,
            operator_name=staff.name,
        )
        return
    await support.send_reply(
        bot, session, staff, target.id, body, channel="telegram", mirror=False
    )


@router.message(F.reply_to_message, F.chat.id == SUPPORT_TARGET)
async def admin_reply(
    message: Message, bot: Bot, session: AsyncSession, user: User
) -> None:
    if SUPPORT_TARGET is None or message.from_user is None or message.from_user.is_bot:
        return
    if message.text and message.text.startswith("/"):
        return
    staff = await support.get_staff(session, user.id)
    if staff is None:
        return
    user_id = await repo.resolve_relay(session, message.reply_to_message.message_id)
    if user_id is None:
        return
    target = await repo.get_user(session, user_id)
    if target is None:
        return
    body = message.text or message.caption
    if body is None:
        await notify.relay_admin_to_user(bot, target, message)
        body = "[вложение]"
        await repo.record_support_message(
            session,
            target.id,
            "outbound",
            body,
            "telegram",
            operator_telegram_id=staff.id,
            operator_name=staff.name,
        )
        return
    await support.send_reply(bot, session, staff, target.id, body, channel="telegram", mirror=False)
