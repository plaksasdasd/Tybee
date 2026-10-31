from __future__ import annotations

from enum import Enum


class UserState(str, Enum):
    MENU = "menu"
    AWAIT_REQUEST = "await_request"
    CHAT = "chat"
    ADMIN_ADD_STAFF = "admin_add_staff"


class RequestStatus(str, Enum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"
