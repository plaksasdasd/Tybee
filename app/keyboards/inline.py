from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.covers.loader import cover_button, cover_text
from app.db.models import MysteryBox


def main_menu(cover_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=cover_button(cover_id, "BTN_NEW_ORDER"), callback_data="order:start")
    kb.button(text=cover_button(cover_id, "BTN_PROFILE"), callback_data="profile:open")
    kb.button(text=cover_button(cover_id, "BTN_SUPPORT"), callback_data="support:open")
    kb.adjust(1)
    return kb.as_markup()


def back_to_menu(cover_id: int) -> InlineKeyboardMarkup:
    """A single "back to menu" button; reused wherever it is the only action."""
    kb = InlineKeyboardBuilder()
    kb.button(text=cover_button(cover_id, "BTN_MENU"), callback_data="back:menu")
    return kb.as_markup()


def back_button(cover_id: int, target: str) -> InlineKeyboardMarkup:
    """A single "Back" button returning the client to the previous step."""
    kb = InlineKeyboardBuilder()
    kb.button(text=cover_button(cover_id, "BTN_BACK"), callback_data=target)
    return kb.as_markup()


mystery_empty = back_to_menu
cancel_to_menu = back_to_menu
review_pending = back_to_menu


def profile_menu(cover_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=cover_button(cover_id, "BTN_MY_ORDERS"), callback_data="profile:orders")
    kb.button(text=cover_button(cover_id, "BTN_OFFER"), url=cover_text(cover_id, "OFFER_URL"))
    kb.button(text=cover_button(cover_id, "BTN_MENU"), callback_data="back:menu")
    kb.adjust(1)
    return kb.as_markup()


def back_to_profile(cover_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=cover_button(cover_id, "BTN_BACK"), callback_data="profile:open")
    return kb.as_markup()


def order_choice(cover_id: int) -> InlineKeyboardMarkup:
    """Wide «Персональный заказ», narrow «По артикулу» + «Mystery Box», wide «Назад»."""
    kb = InlineKeyboardBuilder()
    kb.button(text=cover_button(cover_id, "BTN_ORDER_PERSONAL"), callback_data="order:personal")
    kb.button(text=cover_button(cover_id, "BTN_ORDER_ARTICLE"), callback_data="order:article")
    kb.button(text=cover_button(cover_id, "BTN_ORDER_MYSTERY"), callback_data="order:mystery")
    kb.button(text=cover_button(cover_id, "BTN_BACK"), callback_data="back:menu")
    kb.adjust(1, 2, 1)
    return kb.as_markup()


def article_choice(cover_id: int) -> InlineKeyboardMarkup:
    """Narrow «Да, есть артикул» + «Нет, помогите найти», wide «Назад»."""
    kb = InlineKeyboardBuilder()
    kb.button(text=cover_button(cover_id, "BTN_HAS_ARTICLE"), callback_data="order:has_article")
    kb.button(text=cover_button(cover_id, "BTN_NO_ARTICLE"), callback_data="order:personal")
    kb.button(text=cover_button(cover_id, "BTN_BACK"), callback_data="order:start")
    kb.adjust(2, 1)
    return kb.as_markup()


def mystery_boxes(cover_id: int, boxes: list[MysteryBox]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for box in boxes:
        kb.button(
            text=cover_button(cover_id, "BTN_MYSTERY_ITEM", title=box.title, price=box.price),
            callback_data=f"mystery:item:{box.id}",
        )
    kb.button(text=cover_button(cover_id, "BTN_BACK"), callback_data="order:start")
    kb.adjust(1)
    return kb.as_markup()


def payment_actions(cover_id: int, payment_url: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=cover_button(cover_id, "BTN_PAY_ONLINE"), url=payment_url)
    kb.button(text=cover_button(cover_id, "BTN_MENU"), callback_data="menu:open")
    kb.adjust(1)
    return kb.as_markup()


def payment_reminder(cover_id: int, payment_url: str | None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if payment_url:
        kb.button(text=cover_button(cover_id, "BTN_PAY_ONLINE"), url=payment_url)
    kb.button(text="🗑 Удалить", callback_data="reminder:delete")
    kb.adjust(1)
    return kb.as_markup()


def catalog_rejected(cover_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=cover_button(cover_id, "BTN_PERSONAL_APPLICATION"), callback_data="order:personal")
    kb.button(text=cover_button(cover_id, "BTN_MENU"), callback_data="menu:open")
    kb.adjust(1)
    return kb.as_markup()


def open_dialog(cover_id: int, order_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=cover_button(cover_id, "BTN_OPEN_DIALOG"), callback_data=f"dialog:open:{order_id}")
    kb.button(text=cover_button(cover_id, "BTN_HOME"), callback_data="menu:open")
    kb.adjust(1)
    return kb.as_markup()


def in_dialog(cover_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=cover_button(cover_id, "BTN_CLOSE_DIALOG"), callback_data="dialog:close")
    return kb.as_markup()


def pay_fine(cover_id: int, fine: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=cover_button(cover_id, "BTN_PAY_FINE", fine=fine), callback_data="unban:pay")
    return kb.as_markup()


def pay_fine_url(cover_id: int, url: str, fine: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=cover_button(cover_id, "BTN_PAY_FINE", fine=fine), url=url)
    return kb.as_markup()


def pay_offer(cover_id: int, order_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=cover_button(cover_id, "BTN_TO_PAYMENT"), callback_data=f"pay:proceed:{order_id}")
    kb.button(text=cover_button(cover_id, "BTN_MENU"), callback_data="menu:open")
    kb.adjust(1)
    return kb.as_markup()
