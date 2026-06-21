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
