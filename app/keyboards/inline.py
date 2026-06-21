from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app import texts


def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_NEW_ORDER, callback_data="order:start")
    kb.button(text=texts.BTN_PROFILE, callback_data="profile:open")
    kb.button(text=texts.BTN_SUPPORT, callback_data="support:open")
    # Wide "Оформить заказ" on top, then Профиль + Поддержка in one row.
    kb.adjust(1, 2)
    return kb.as_markup()


def back_to_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_MENU, callback_data="menu:open")
    return kb.as_markup()


def profile_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_MY_ORDERS, callback_data="profile:orders")
    kb.button(text=texts.BTN_OFFER, callback_data="profile:offer")
    kb.button(text=texts.BTN_MENU, callback_data="menu:open")
    kb.adjust(1)
    return kb.as_markup()


def back_to_profile() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_BACK, callback_data="profile:open")
    kb.button(text=texts.BTN_MENU, callback_data="menu:open")
    kb.adjust(2)
    return kb.as_markup()


def article_choice() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_HAS_ARTICLE, callback_data="order:has_article")
    kb.button(text=texts.BTN_NO_ARTICLE, callback_data="order:personal")
    kb.button(text=texts.BTN_MENU, callback_data="menu:open")
    kb.adjust(1)
    return kb.as_markup()


def cancel_to_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_MENU, callback_data="menu:open")
    return kb.as_markup()


def payment_actions(order_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_PAID, callback_data=f"pay:done:{order_id}")
    kb.button(text=texts.BTN_MENU, callback_data="menu:open")
    kb.adjust(1)
    return kb.as_markup()


def catalog_rejected() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_PERSONAL, callback_data="order:personal")
    kb.button(text=texts.BTN_MENU, callback_data="menu:open")
    kb.adjust(1)
    return kb.as_markup()


def open_dialog(order_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_OPEN_DIALOG, callback_data=f"dialog:open:{order_id}")
    kb.button(text=texts.BTN_MENU, callback_data="menu:open")
    kb.adjust(1)
    return kb.as_markup()


def in_dialog() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_CLOSE_DIALOG, callback_data="dialog:close")
    return kb.as_markup()


def review_pending() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_MENU, callback_data="menu:open")
    return kb.as_markup()


def admin_payment(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_CONFIRM_PAYMENT, callback_data=f"adm:confirm:{order_id}"
                ),
                InlineKeyboardButton(
                    text=texts.BTN_REJECT_PAYMENT, callback_data=f"adm:reject:{order_id}"
                ),
            ]
        ]
    )
