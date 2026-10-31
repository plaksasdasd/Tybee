from __future__ import annotations

from aiogram import Router

from support_bot.handlers import admin, client, orders, staff, text


def build_router() -> Router:
    root = Router(name="support_root")
    root.include_router(admin.router)
    root.include_router(client.router)
    root.include_router(staff.router)
    root.include_router(orders.router)
    root.include_router(text.router)
    return root
