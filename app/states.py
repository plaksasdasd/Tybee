from __future__ import annotations

from enum import Enum


class UserState(str, Enum):
    """Conversational mode of a user, persisted in the database.

    Navigation between menus happens through inline callbacks; only states that
    expect free-form text input from the user are tracked here.
    """

    MENU = "menu"
    AWAIT_ARTICLE = "await_article"
    AWAIT_PERSONAL = "await_personal"
    DIALOG = "dialog"
    # Admin is entering a new catalog article via the /admin panel.
    ADMIN_ADD_PRODUCT = "admin_add_product"
    # Admin is importing catalog articles from an xlsx file.
    ADMIN_IMPORT_PRODUCTS = "admin_import_products"
    # Admin is entering a new Mystery Box variant via the /admin panel.
    ADMIN_ADD_BOX = "admin_add_box"
    # Admin is composing a broadcast message.
    ADMIN_BROADCAST = "admin_broadcast"
