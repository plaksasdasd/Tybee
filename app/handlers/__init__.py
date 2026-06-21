from __future__ import annotations

from aiogram import Router

from app.handlers import (
    admin,
    article,
    dialog,
    payment,
    personal,
    profile,
    start,
    support,
    text,
)


def build_router() -> Router:
    root = Router(name="root")
    # Order matters: admin (incl. support-chat replies) and specific callback
    # routers must precede the catch-all text router.
    root.include_router(admin.router)
    root.include_router(start.router)
    root.include_router(profile.router)
    root.include_router(article.router)
    root.include_router(payment.router)
    root.include_router(personal.router)
    root.include_router(dialog.router)
    root.include_router(support.router)
    root.include_router(text.router)
    return root
