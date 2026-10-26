from __future__ import annotations

import html
from dataclasses import dataclass

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.assets import Banner
from app.db.models import FULFILLMENT_STATUSES, Order, OrderStatus, OrderType, User
from app.keyboards import inline
from app.services import notify
from app.services.navigation import show_screen
from app.services.screens import send_order_invoice


@dataclass(frozen=True)
class Staff:
    id: int
    name: str
    is_admin: bool


class SupportError(RuntimeError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


async def get_staff(session: AsyncSession, telegram_id: int) -> Staff | None:
    user = await repo.get_user(session, telegram_id)
    operator = await repo.get_support_operator(session, telegram_id)
    is_admin = user is not None and repo.is_admin(user)
    if not is_admin and (operator is None or not operator.is_active):
        return None
    fallback = (user.full_name or user.username or "Оператор") if user else "Оператор"
    name = operator.display_name.strip() if operator and operator.display_name.strip() else fallback
    return Staff(id=telegram_id, name=name[:80], is_admin=is_admin)


async def require_staff(session: AsyncSession, telegram_id: int) -> Staff:
    staff = await get_staff(session, telegram_id)
    if staff is None:
        raise SupportError("Недостаточно прав", 403)
    return staff


async def _client(session: AsyncSession, client_id: int) -> User:
    client = await repo.get_user(session, client_id)
    if client is None:
        raise SupportError("Клиент не найден", 404)
    return client


async def _order(session: AsyncSession, order_id: int) -> tuple[Order, User]:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise SupportError("Заказ не найден", 404)
    return order, await _client(session, order.user_id)


async def _system(
    session: AsyncSession, client: User, staff: Staff, body: str, channel: str
) -> None:
    await repo.record_support_message(
        session,
        client.id,
        "system",
        body,
        channel,
        operator_telegram_id=staff.id,
        operator_name=staff.name,
    )


async def send_reply(
    bot: Bot,
    session: AsyncSession,
    staff: Staff,
    client_id: int,
    body: str,
    *,
    channel: str,
    mirror: bool = True,
) -> None:
    body = body.strip()
    if not body:
        raise SupportError("Введите сообщение", 422)
    if len(body) > 3500:
        raise SupportError("Сообщение слишком длинное (максимум 3500 символов)", 422)
    client = await _client(session, client_id)
    if client.source == "avito":
        delivered = await notify.send_avito_text(client, f"{staff.name}:\n{body}")
    else:
        await bot.send_message(
            client.id,
            f"<b>{html.escape(staff.name)}:</b>\n{html.escape(body)}",
            parse_mode="HTML",
        )
        delivered = True
    if not delivered:
        raise SupportError("Не удалось доставить сообщение клиенту", 502)
    await repo.record_support_message(
        session,
        client.id,
        "outbound",
        body,
        channel,
        operator_telegram_id=staff.id,
        operator_name=staff.name,
    )
    if mirror:
        await notify.send_to_client_thread(
            bot,
            session,
            f"👤 <b>{html.escape(staff.name)}</b>\n{html.escape(body)}",
            client,
        )


async def create_support_order(
    bot: Bot,
    session: AsyncSession,
    staff: Staff,
    client_id: int,
    title: str,
    description: str,
    price: int,
    *,
    channel: str,
) -> Order:
    title = title.strip()
    description = description.strip()
    if not title or len(title) > 256:
        raise SupportError("Название обязательно и должно быть не длиннее 256 символов", 422)
    if len(description) > 4000:
        raise SupportError("Описание слишком длинное", 422)
    if not isinstance(price, int) or isinstance(price, bool) or not 1 <= price <= 1_000_000_000:
        raise SupportError("Сумма должна быть целым положительным числом", 422)
    client = await _client(session, client_id)
    order = await repo.create_order(
        session,
        client,
        type=OrderType.PERSONAL.value,
        status=OrderStatus.AWAITING_PAYMENT.value,
        title=title,
        description=description or None,
        price=price,
    )
    await send_order_invoice(bot, session, client, order)
    await notify.notify_new_order(bot, session, client, order)
    await _system(
        session, client, staff, f"Выставлен счёт по заказу #{order.id} на {price} ₽", channel
    )
    return order


async def decide_payment(
    bot: Bot,
    session: AsyncSession,
    staff: Staff,
    order_id: int,
    approved: bool,
    *,
    channel: str,
) -> Order:
    order, client = await _order(session, order_id)
    allowed = {OrderStatus.PAYMENT_REVIEW.value, OrderStatus.AWAITING_PAYMENT.value}
    if order.status not in allowed:
        raise SupportError("По этому заказу уже принято решение", 409)
    await repo.set_order_status(
        session, order, OrderStatus.APPROVED if approved else OrderStatus.REJECTED
    )
    if client.source == "avito":
        await notify.send_avito_payment_decision(client, order, approved=approved)
    elif approved:
        await show_screen(
            bot,
            session,
            client,
            Banner.APPROVED,
            texts.APPROVED.format(order_id=order.id),
            inline.open_dialog(order.id),
        )
    else:
        await show_screen(
            bot,
            session,
            client,
            Banner.REJECTED,
            texts.REJECTED_PAYMENT.format(order_id=order.id),
            inline.back_to_menu(),
        )
    decision = "оплата подтверждена ✅" if approved else "заявка отклонена ❎"
    await notify.send_to_client_thread(
        bot,
        session,
        texts.ADMIN_PAYMENT_DECISION.format(order_id=order.id, decision=decision),
        client,
    )
    if approved:
        await notify.post_order_panel(bot, session, client, order)
    await _system(session, client, staff, f"Заказ #{order.id}: {decision}", channel)
    return order


async def change_order_status(
    bot: Bot,
    session: AsyncSession,
    staff: Staff,
    order_id: int,
    status: OrderStatus,
    *,
    channel: str,
) -> Order:
    if status not in FULFILLMENT_STATUSES:
        raise SupportError("Неизвестный статус", 422)
    order, client = await _order(session, order_id)
    allowed = {OrderStatus.APPROVED.value} | {item.value for item in FULFILLMENT_STATUSES}
    if order.status not in allowed:
        raise SupportError("Сначала подтвердите оплату заказа", 409)
    if order.status == status.value:
        return order
    await repo.set_order_status(session, order, status)
    await notify.notify_order_status(bot, client.id, order, session=session)
    await _system(
        session,
        client,
        staff,
        f"Заказ #{order.id}: статус изменён на «{notify.status_label(status.value)}»",
        channel,
    )
    return order


async def set_client_banned(
    session: AsyncSession,
    staff: Staff,
    client_id: int,
    banned: bool,
    *,
    channel: str,
) -> User:
    client = await _client(session, client_id)
    await repo.set_banned(session, client, banned)
    await _system(
        session,
        client,
        staff,
        "Клиент заблокирован" if banned else "Клиент разблокирован",
        channel,
    )
    return client
