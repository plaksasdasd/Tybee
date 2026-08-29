from __future__ import annotations

import shlex

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.assets import Banner
from app.config import get_settings
from app.db.models import OrderStatus, OrderType, User
from app.keyboards import inline
from app.services import notify
from app.services.navigation import show_screen
from app.services.notify import relay_admin_to_user
from app.services.screens import send_order_invoice

router = Router(name="admin")

SUPPORT_TARGET = get_settings().support_target
SUPPORT_CHAT_ID = get_settings().support_chat_id

# Support commands may be issued in the support chat (targeting a thread/reply)
# or in an admin's private chat (targeting an explicit id/@username).
_IN_SUPPORT_OR_DM = (F.chat.id == SUPPORT_TARGET) | (F.chat.type == "private")


async def _resolve_target_user(session: AsyncSession, message: Message) -> User | None:
    """Find the client a support action targets: by thread, then by reply."""
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
    """Resolve a /ban|/pardon target: explicit id/@username arg, else thread/reply.

    Returns (user, error_text). When the id is unknown the user row is created so
    the block takes effect as soon as the client opens the bot.
    """
    token = args.split()[0] if args else ""
    if token:
        if token.startswith("@") or not token.lstrip("-").isdigit():
            target = await repo.get_user_by_username(session, token)
            if target is None:
                return None, texts.TARGET_USERNAME_NOT_FOUND.format(username=token)
            return target, None
        return await repo.get_or_create_user_by_id(session, int(token)), None

    target = await _resolve_target_user(session, message)
    return target, None


# ---- Payment decision (buttons on the payment notification) ----
async def _decide(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, approve: bool
) -> None:
    if not repo.is_admin(user):
        await call.answer("Недостаточно прав", show_alert=True)
        return

    order_id = int(call.data.split(":")[2])
    order = await repo.get_order(session, order_id)
    if order is None:
        await call.answer("Заказ не найден", show_alert=True)
        return

    customer = await session.get(User, order.user_id)
    if customer is None:
        await call.answer("Клиент не найден", show_alert=True)
        return

    if approve:
        await repo.set_order_status(session, order, OrderStatus.APPROVED)
        if customer.source == "avito":
            await notify.send_avito_payment_decision(customer, order, approved=True)
        else:
            await show_screen(
                bot,
                session,
                customer,
                Banner.APPROVED,
                texts.APPROVED.format(order_id=order.id),
                inline.open_dialog(order.id),
            )
        decision = "оплата подтверждена ✅"
    else:
        await repo.set_order_status(session, order, OrderStatus.REJECTED)
        if customer.source == "avito":
            await notify.send_avito_payment_decision(customer, order, approved=False)
        else:
            await show_screen(
                bot,
                session,
                customer,
                Banner.REJECTED,
                texts.REJECTED_PAYMENT.format(order_id=order.id),
                inline.back_to_menu(),
            )
        decision = "заявка отклонена ❎"

    if call.message is not None:
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
    await call.answer(decision)
    await notify.send_to_client_thread(
        bot,
        session,
        texts.ADMIN_PAYMENT_DECISION.format(order_id=order.id, decision=decision),
        customer,
    )
    # On approval, post the fulfillment status control panel into the thread.
    if approve:
        await notify.post_order_panel(bot, session, customer, order)


@router.callback_query(F.data.startswith("adm:confirm:"))
async def confirm(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    await _decide(call, bot, session, user, approve=True)


@router.callback_query(F.data.startswith("adm:reject:"))
async def reject(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    await _decide(call, bot, session, user, approve=False)


# ---- Order fulfillment status buttons ----
@router.callback_query(F.data.startswith("adm:status:"))
async def change_status(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        await call.answer("Недостаточно прав", show_alert=True)
        return
    _, _, order_id_raw, status_raw = call.data.split(":")
    order = await repo.get_order(session, int(order_id_raw))
    if order is None:
        await call.answer("Заказ не найден", show_alert=True)
        return
    try:
        status = OrderStatus(status_raw)
    except ValueError:
        await call.answer("Неизвестный статус", show_alert=True)
        return

    await repo.set_order_status(session, order, status)
    await notify.notify_order_status(bot, order.user_id, order, session=session)

    if call.message is not None:
        try:
            await call.message.edit_text(
                texts.ORDER_PANEL.format(
                    order_id=order.id,
                    title=order.title or "Заказ",
                    status=notify.status_label(order.status),
                ),
                parse_mode="HTML",
                reply_markup=inline.order_status(order.id, order.status),
            )
        except TelegramBadRequest:
            pass
    await call.answer(f"Статус: {notify.status_label(order.status)}")


# ---- Support commands (run inside a client's thread / as a reply) ----
@router.message(Command("order"), F.chat.id == SUPPORT_TARGET)
async def cmd_order(
    message: Message, command: CommandObject, bot: Bot, session: AsyncSession, user: User
) -> None:
    if not repo.is_admin(user):
        await message.reply(texts.CMD_NOT_ALLOWED)
        return
    if not command.args:
        await message.reply(texts.ORDER_USAGE, parse_mode="HTML")
        return

    raw = command.args.strip()
    title: str = ""
    description: str = ""
    price: int | None = None

    # Поддержка разделителя "|" :  title | description | price  или  title | price
    if "|" in raw:
        parts = [p.strip() for p in raw.split("|")]
        if not parts[-1].isdigit():
            await message.reply(texts.ORDER_USAGE, parse_mode="HTML")
            return
        price = int(parts[-1])
        if len(parts) == 2:
            title = parts[0]
            description = ""
        elif len(parts) == 3:
            title, description = parts[0], parts[1]
        else:
            title = parts[0]
            description = " | ".join(parts[1:-1])
        if not title:
            await message.reply(texts.ORDER_USAGE, parse_mode="HTML")
            return
    else:
        try:
            tokens = shlex.split(raw)
        except ValueError:
            tokens = raw.split()
        if len(tokens) < 2 or not tokens[-1].isdigit():
            await message.reply(texts.ORDER_USAGE, parse_mode="HTML")
            return
        price = int(tokens[-1])
        has_quotes = any(q in raw for q in ('"', "'", "“", "”", "‘", "’"))
        if has_quotes:
            # Кавычки: первый токен = название (может содержать пробелы), середина = описание
            title = tokens[0]
            description = " ".join(tokens[1:-1])
        else:
            # Без кавычек: многословное название склеивается.
            # Для описания используйте кавычки или "|":  /order "Название" Описание 15000  или  /order Название | Описание | 15000
            if len(tokens) == 2:
                title = tokens[0]
                description = ""
            else:
                title = " ".join(tokens[:-1])
                description = ""

    target = await _resolve_target_user(session, message)
    if target is None:
        await message.reply(texts.ORDER_NEED_THREAD)
        return

    if not title or price is None:
        await message.reply(texts.ORDER_USAGE, parse_mode="HTML")
        return

    order = await repo.create_order(
        session,
        target,
        type=OrderType.PERSONAL.value,
        status=OrderStatus.AWAITING_PAYMENT.value,
        title=title,
        description=description or None,
        price=price,
    )
    await send_order_invoice(bot, session, target, order)
    await notify.notify_new_order(bot, session, target, order)
    await message.reply(f"Счёт по заказу #{order.id} отправлен клиенту.")


@router.message(Command("ban"), _IN_SUPPORT_OR_DM)
async def cmd_ban(
    message: Message, command: CommandObject, session: AsyncSession, user: User
) -> None:
    if not repo.is_admin(user):
        await message.reply(texts.CMD_NOT_ALLOWED)
        return
    target, error = await _resolve_command_target(session, message, command.args)
    if error is not None:
        await message.reply(error, parse_mode="HTML")
        return
    if target is None:
        await message.reply(texts.BAN_USAGE, parse_mode="HTML")
        return
    if target.is_banned:
        await message.reply(texts.ALREADY_BANNED)
        return
    await repo.set_banned(session, target, True)
    await message.reply(texts.BAN_DONE + f"\n{repo.display_name(target) or target.id}")


@router.message(Command("pardon"), _IN_SUPPORT_OR_DM)
async def cmd_pardon(
    message: Message, command: CommandObject, session: AsyncSession, user: User
) -> None:
    if not repo.is_admin(user):
        await message.reply(texts.CMD_NOT_ALLOWED)
        return
    target, error = await _resolve_command_target(session, message, command.args)
    if error is not None:
        await message.reply(error, parse_mode="HTML")
        return
    if target is None:
        await message.reply(texts.PARDON_USAGE, parse_mode="HTML")
        return
    if not target.is_banned:
        await message.reply(texts.NOT_BANNED)
        return
    await repo.set_banned(session, target, False)
    await message.reply(texts.PARDON_DONE + f"\n{repo.display_name(target) or target.id}")


# ---- Relaying support messages back to the client ----
@router.message(F.chat.id == SUPPORT_CHAT_ID)
async def thread_message(message: Message, bot: Bot, session: AsyncSession, user: User) -> None:
    """Any (non-command) message an admin posts in a client's thread → client."""
    if not repo.is_admin(user):
        return
    if message.from_user is None or message.from_user.is_bot:
        return
    if message.text and message.text.startswith("/"):
        return
    target = await _resolve_target_user(session, message)
    if target is None:
        return
    await relay_admin_to_user(bot, target, message)


@router.message(F.reply_to_message, F.chat.id == SUPPORT_TARGET)
async def admin_reply(
    message: Message, bot: Bot, session: AsyncSession, user: User
) -> None:
    """Relay an admin's reply (in a non-forum support chat) back to the customer."""
    if SUPPORT_TARGET is None:
        return
    if not repo.is_admin(user):
        return
    if message.from_user is None or message.from_user.is_bot:
        return
    if message.text and message.text.startswith("/"):
        return
    user_id = await repo.resolve_relay(session, message.reply_to_message.message_id)
    if user_id is None:
        return
    target = await repo.get_user(session, user_id)
    if target is None:
        return
    await relay_admin_to_user(bot, target, message)
