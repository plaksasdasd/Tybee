from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app import texts
from app.db.models import MysteryBox, Product


def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_NEW_ORDER, callback_data="order:start")
    kb.button(text=texts.BTN_PROFILE, callback_data="profile:open")
    kb.button(text=texts.BTN_SUPPORT, callback_data="support:open")
    kb.adjust(1)
    return kb.as_markup()


def back_to_menu() -> InlineKeyboardMarkup:
    """A single "back to menu" button; reused wherever it is the only action."""
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_MENU, callback_data="back:menu")
    return kb.as_markup()


def back_button(target: str) -> InlineKeyboardMarkup:
    """A single "Back" button returning the client to the previous step."""
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_BACK, callback_data=target)
    return kb.as_markup()


mystery_empty = back_to_menu
cancel_to_menu = back_to_menu
review_pending = back_to_menu


def profile_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_MY_ORDERS, callback_data="profile:orders")
    kb.button(text=texts.BTN_OFFER, url=texts.BTN_OFFER_URL)
    kb.button(text=texts.BTN_MENU, callback_data="back:menu")
    kb.adjust(1)
    return kb.as_markup()


def back_to_profile() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_BACK, callback_data="profile:open")
    return kb.as_markup()


def order_choice() -> InlineKeyboardMarkup:
    """Wide «Персональный заказ», narrow «По артикулу» + «Mystery Box», wide «Назад»."""
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_ORDER_PERSONAL, callback_data="order:personal")
    kb.button(text=texts.BTN_ORDER_ARTICLE, callback_data="order:article")
    kb.button(text=texts.BTN_ORDER_MYSTERY, callback_data="order:mystery")
    kb.button(text=texts.BTN_BACK, callback_data="back:menu")
    kb.adjust(1, 2, 1)
    return kb.as_markup()


def article_choice() -> InlineKeyboardMarkup:
    """Narrow «Да, есть артикул» + «Нет, помогите найти», wide «Назад»."""
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_HAS_ARTICLE, callback_data="order:has_article")
    kb.button(text=texts.BTN_NO_ARTICLE, callback_data="order:personal")
    kb.button(text=texts.BTN_BACK, callback_data="order:start")
    kb.adjust(2, 1)
    return kb.as_markup()


def mystery_boxes(boxes: list[MysteryBox]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for box in boxes:
        kb.button(
            text=f"🎁 {box.title} — {box.price} ₽",
            callback_data=f"mystery:item:{box.id}",
        )
    kb.button(text=texts.BTN_BACK, callback_data="order:start")
    kb.adjust(1)
    return kb.as_markup()


def payment_actions(payment_url: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_PAY_ONLINE, url=payment_url)
    kb.button(text=texts.BTN_MENU, callback_data="menu:open")
    kb.adjust(1)
    return kb.as_markup()


def catalog_rejected() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_PERSONAL_APPLICATION, callback_data="order:personal")
    kb.button(text=texts.BTN_MENU, callback_data="menu:open")
    kb.adjust(1)
    return kb.as_markup()


def open_dialog(order_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_OPEN_DIALOG, callback_data=f"dialog:open:{order_id}")
    kb.button(text=texts.BTN_HOME, callback_data="menu:open")
    kb.adjust(1)
    return kb.as_markup()


def in_dialog() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_CLOSE_DIALOG, callback_data="dialog:close")
    return kb.as_markup()


def admin_panel() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_ADM_PRODUCTS, callback_data="adm:prod:list")
    kb.button(text=texts.BTN_ADM_BOXES, callback_data="adm:box:list")
    kb.button(text=texts.BTN_ADM_STATS, callback_data="adm:stats")
    kb.button(text=texts.BTN_ADM_BROADCAST, callback_data="adm:broadcast")
    kb.button(text=texts.BTN_ADM_CLOSE, callback_data="adm:panel:close")
    kb.adjust(1)
    return kb.as_markup()


def admin_broadcast_confirm() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_ADM_BROADCAST_SEND, callback_data="adm:broadcast:send")
    kb.button(text=texts.BTN_ADM_CANCEL, callback_data="adm:broadcast:cancel")
    kb.adjust(1)
    return kb.as_markup()


def admin_boxes(boxes: list[MysteryBox]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for box in boxes:
        mark = "🟢" if box.available else "🔴"
        kb.button(
            text=f"{mark} {box.title} · {box.price} ₽",
            callback_data=f"adm:box:view:{box.id}",
        )
    kb.button(text=texts.BTN_ADM_ADD_BOX, callback_data="adm:box:add")
    kb.button(text=texts.BTN_ADM_BACK, callback_data="adm:panel:open")
    kb.adjust(1)
    return kb.as_markup()


def admin_box_view(box: MysteryBox) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    toggle = texts.BTN_ADM_TOGGLE_OFF if box.available else texts.BTN_ADM_TOGGLE_ON
    kb.button(text=toggle, callback_data=f"adm:box:toggle:{box.id}")
    kb.button(text=texts.BTN_ADM_DELETE, callback_data=f"adm:box:del:{box.id}")
    kb.button(text=texts.BTN_ADM_BACK_BOXES, callback_data="adm:box:list")
    kb.adjust(1)
    return kb.as_markup()


def admin_add_box_cancel() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_ADM_CANCEL, callback_data="adm:box:list")
    return kb.as_markup()


def admin_products(products: list[Product], page: int = 0) -> InlineKeyboardMarkup:
    """Paginated product list: 10 per page, импорт/создание — первые кнопки."""
    per_page = 10
    total = len(products)
    total_pages = (total + per_page - 1) // per_page or 1
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    end = start + per_page
    page_products = products[start:end]

    # Первые кнопки — импорт и создание (требование), затем список
    keyboard: list[list[InlineKeyboardButton]] = []
    keyboard.append([InlineKeyboardButton(text=texts.BTN_ADM_IMPORT, callback_data="adm:prod:import")])
    keyboard.append([InlineKeyboardButton(text=texts.BTN_ADM_ADD_PRODUCT, callback_data="adm:prod:add")])

    for product in page_products:
        mark = "🟢" if product.available else "🔴"
        title = product.title[:24] + "…" if len(product.title) > 24 else product.title
        keyboard.append(
            [InlineKeyboardButton(text=f"{mark} {product.article} · {title}", callback_data=f"adm:prod:view:{product.id}")]
        )

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"adm:prod:list:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="adm:prod:nop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="➡️ Далее", callback_data=f"adm:prod:list:{page + 1}"))
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton(text=texts.BTN_ADM_BACK, callback_data="adm:panel:open")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def admin_product_view(product: Product) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    toggle = texts.BTN_ADM_TOGGLE_OFF if product.available else texts.BTN_ADM_TOGGLE_ON
    kb.button(text=toggle, callback_data=f"adm:prod:toggle:{product.id}")
    kb.button(text=texts.BTN_ADM_DELETE, callback_data=f"adm:prod:del:{product.id}")
    kb.button(text=texts.BTN_ADM_BACK_LIST, callback_data="adm:prod:list")
    kb.adjust(1)
    return kb.as_markup()


def admin_add_cancel() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_ADM_CANCEL, callback_data="adm:panel:open")
    return kb.as_markup()


def admin_import_cancel() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_ADM_CANCEL, callback_data="adm:prod:list")
    return kb.as_markup()


def admin_back() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_ADM_BACK, callback_data="adm:panel:open")
    return kb.as_markup()


def pay_fine(fine: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_PAY_FINE.format(fine=fine), callback_data="unban:pay")
    return kb.as_markup()


def pay_fine_url(url: str, fine: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_PAY_FINE.format(fine=fine), url=url)
    return kb.as_markup()


def pay_offer(order_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_TO_PAYMENT, callback_data=f"pay:proceed:{order_id}")
    kb.button(text=texts.BTN_MENU, callback_data="menu:open")
    kb.adjust(1)
    return kb.as_markup()