from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from support_bot import repositories as repo
from support_bot.keyboards import main_menu, request_actions, request_list
from support_bot.models import BotUser, SupportRequest
from support_bot.services import (
    esc,
    notify_new_request,
    request_details,
    safe_body_message,
    safe_message,
    sync_claim_notifications,
)
from support_bot.source_repository import close_source_request, queue_source_outbox
from support_bot.states import RequestStatus, UserState

router = Router(name="client")


async def _menu(bot: Bot, session: AsyncSession, user: BotUser) -> None:
    user.state = UserState.MENU.value
    user.active_request_id = None
    staff = await repo.is_staff(session, user.id)
    await bot.send_message(
        user.id,
        "<b>Поддержка Тайби</b>\n\nСоздайте обращение, и сотрудник ответит вам здесь.",
        parse_mode="HTML",
        reply_markup=main_menu(staff=staff, admin=repo.is_admin(user.id)),
    )


@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: Message, bot: Bot, session: AsyncSession, user: BotUser) -> None:
    await _menu(bot, session, user)


@router.callback_query(F.data == "menu:open")
async def menu_open(call: CallbackQuery, bot: Bot, session: AsyncSession, user: BotUser) -> None:
    await _menu(bot, session, user)
    await call.answer()


@router.callback_query(F.data == "request:new")
async def new_request(call: CallbackQuery, session: AsyncSession, user: BotUser) -> None:
    user.state = UserState.AWAIT_REQUEST.value
    user.active_request_id = None
    if call.message is not None:
        await call.message.answer(
            "Опишите вопрос одним текстовым сообщением. После отправки сотрудники увидят обращение."
        )
    await call.answer()


async def _requests(bot: Bot, session: AsyncSession, user: BotUser) -> None:
    staff = await repo.is_staff(session, user.id)
    if staff:
        requests = await repo.list_staff_requests(session, user.id, admin=repo.is_admin(user.id))
        text = "Доступные и назначенные обращения:" if requests else "Сейчас нет открытых обращений."
    else:
        requests = await repo.list_customer_requests(session, user.id)
        text = "Ваши обращения:" if requests else "У вас пока нет обращений."
    await bot.send_message(user.id, text, reply_markup=request_list(requests, staff=staff))


@router.message(Command("requests"), F.chat.type == "private")
async def cmd_requests(message: Message, bot: Bot, session: AsyncSession, user: BotUser) -> None:
    await _requests(bot, session, user)


@router.callback_query(F.data == "request:list")
async def requests_callback(call: CallbackQuery, bot: Bot, session: AsyncSession, user: BotUser) -> None:
    await _requests(bot, session, user)
    await call.answer()


def _can_view(request: SupportRequest, user: BotUser, *, staff: bool) -> bool:
    if request.customer_id == user.id or repo.is_admin(user.id):
        return True
    return staff and request.assigned_to == user.id


async def show_request(
    bot: Bot, session: AsyncSession, user: BotUser, request: SupportRequest
) -> None:
    messages = await repo.list_messages(session, request.id, limit=6)
    can_view_chat = request.customer_id == user.id or request.assigned_to == user.id
    if request.status != RequestStatus.CLOSED.value and can_view_chat:
        user.state = UserState.CHAT.value
        user.active_request_id = request.id
    else:
        user.state = UserState.MENU.value
        user.active_request_id = None
    can_close = _can_view(request, user, staff=await repo.is_staff(session, user.id))
    await bot.send_message(
        user.id,
        request_details(request, messages),
        parse_mode="HTML",
        reply_markup=request_actions(request, can_close=can_close),
    )


@router.callback_query(F.data.startswith("request:open:"))
async def open_request(call: CallbackQuery, bot: Bot, session: AsyncSession, user: BotUser) -> None:
    request = await repo.get_request(session, int(call.data.split(":")[2]))
    staff = await repo.is_staff(session, user.id)
    if request is None:
        await call.answer("Обращение не найдено", show_alert=True)
        return
    if not _can_view(request, user, staff=staff):
        await call.answer("Сначала возьмите обращение в работу", show_alert=True)
        return
    await show_request(bot, session, user, request)
    await call.answer()


@router.callback_query(F.data.startswith("request:close:"))
async def close_request(call: CallbackQuery, bot: Bot, session: AsyncSession, user: BotUser) -> None:
    request = await repo.get_request(session, int(call.data.split(":")[2]))
    staff = await repo.is_staff(session, user.id)
    if request is None:
        await call.answer("Обращение не найдено", show_alert=True)
        return
    if not _can_view(request, user, staff=staff):
        await call.answer("Недостаточно прав", show_alert=True)
        return
    if request.status == RequestStatus.CLOSED.value:
        await call.answer("Обращение уже закрыто", show_alert=True)
        return

    customer_id = request.customer_id
    assigned_to = request.assigned_to
    await repo.add_message(session, request, user.id, "system", "Обращение закрыто")
    await repo.close_request(session, request)
    await sync_claim_notifications(bot, session, request)
    user.state = UserState.MENU.value
    user.active_request_id = None
    if request.source == "main":
        await close_source_request(request)
        if assigned_to is not None and assigned_to != user.id:
            await safe_message(bot, assigned_to, f"Обращение #{request.id} закрыто.")
    else:
        for telegram_id in {customer_id, assigned_to}:
            if telegram_id is not None and telegram_id != user.id:
                await safe_message(bot, telegram_id, f"Обращение #{request.id} закрыто.")
    if call.message is not None:
        await call.message.answer(f"Обращение #{request.id} закрыто.")
    await call.answer("Обращение закрыто")


async def process_new_request(
    bot: Bot, session: AsyncSession, user: BotUser, body: str
) -> None:
    body = body.strip()
    if not 2 <= len(body) <= 3500:
        await safe_message(bot, user.id, "Отправьте текст длиной от 2 до 3500 символов.")
        return
    request = await repo.create_request(session, user, body)
    await bot.send_message(
        user.id,
        f"Обращение <b>#{request.id}</b> создано. Можно продолжить писать здесь.",
        parse_mode="HTML",
        reply_markup=request_actions(request, can_close=True),
    )
    await notify_new_request(bot, session, request, body)


async def process_chat_message(
    bot: Bot, session: AsyncSession, user: BotUser, body: str
) -> None:
    request = await repo.get_request(session, user.active_request_id or 0)
    if request is None or request.status == RequestStatus.CLOSED.value:
        await _menu(bot, session, user)
        return
    body = body.strip()
    if not body or len(body) > 3500:
        await safe_message(bot, user.id, "Отправьте текст длиной до 3500 символов.")
        return

    is_customer = request.customer_id == user.id
    staff = None if is_customer else await repo.get_active_staff_member(session, user)
    if not is_customer and (staff is None or request.assigned_to != user.id):
        await safe_message(bot, user.id, "Это обращение назначено другому сотруднику.")
        return
    sender_kind = "client" if is_customer else "staff"
    await repo.add_message(session, request, user.id, sender_kind, body)

    if request.status == RequestStatus.NEW.value:
        await safe_message(bot, user.id, "Сообщение сохранено. Обращение ещё ожидает сотрудника.")
        return
    if is_customer:
        target = request.assigned_to
        sender = "Клиент"
    else:
        target = request.customer_id
        sender = esc(staff.display_name)
    if not is_customer and request.source == "main":
        await queue_source_outbox(request, f"<b>{sender}:</b>\n{esc(body)}")
    elif target is not None:
        await safe_body_message(bot, target, request.id, sender, body)
