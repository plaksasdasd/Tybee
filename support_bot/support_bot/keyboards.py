from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from support_bot.models import Staff, SupportRequest
from support_bot.states import RequestStatus


def main_menu(*, staff: bool, admin: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✍️ Создать обращение", callback_data="request:new")
    if staff:
        kb.button(text="🛠 Обращения клиентов", callback_data="request:list")
    else:
        kb.button(text="📋 Мои обращения", callback_data="request:list")
    if admin:
        kb.button(text="👥 Сотрудники", callback_data="admin:open")
    kb.adjust(1)
    return kb.as_markup()


def request_list(requests: list[SupportRequest], *, staff: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for request in requests:
        if staff and request.status == RequestStatus.NEW.value:
            label = f"🆕 #{request.id} — взять в работу"
            action = f"request:claim:{request.id}"
        else:
            status = "✅" if request.status == RequestStatus.CLOSED.value else "💬"
            label = f"{status} Обращение #{request.id}"
            action = f"request:open:{request.id}"
        kb.button(text=label, callback_data=action)
    kb.button(text="⬅️ В меню", callback_data="menu:open")
    kb.adjust(1)
    return kb.as_markup()


def request_actions(request: SupportRequest, *, can_close: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if can_close and request.status != RequestStatus.CLOSED.value:
        kb.button(text="❎ Закрыть обращение", callback_data=f"request:close:{request.id}")
    kb.button(text="⬅️ К обращениям", callback_data="request:list")
    kb.adjust(1)
    return kb.as_markup()


def claim_request(request_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🙋 Взять в работу", callback_data=f"request:claim:{request_id}")
    return kb.as_markup()


def payment_actions(order_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить оплату", callback_data=f"order:confirm:{order_id}")
    kb.button(text="❎ Отклонить", callback_data=f"order:reject:{order_id}")
    kb.adjust(1)
    return kb.as_markup()


def order_status_actions(order_id: int, statuses: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for value, label in statuses:
        kb.button(text=label, callback_data=f"order:status:{order_id}:{value}")
    kb.adjust(1)
    return kb.as_markup()


def admin_panel(staff: list[Staff]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for member in staff:
        mark = "🟢" if member.is_active else "🔴"
        kb.button(
            text=f"{mark} {member.display_name} · {member.telegram_id}",
            callback_data=f"admin:toggle:{member.telegram_id}",
        )
    kb.button(text="➕ Добавить / изменить", callback_data="admin:add")
    kb.button(text="⬅️ В меню", callback_data="menu:open")
    kb.adjust(1)
    return kb.as_markup()


def cancel_to_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="❎ Отмена", callback_data="menu:open")
    return kb.as_markup()
