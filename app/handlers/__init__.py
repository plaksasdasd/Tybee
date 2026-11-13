from __future__ import annotations

import copy

from aiogram import Router

from app.handlers import (
    article,
    ban,
    dialog,
    mystery,
    payment,
    personal,
    profile,
    start,
    support,
    text,
)


def _clone_router(template: Router) -> Router:
    """A fresh Router with the same handlers.

    The handler modules define their routers once at import time, and an aiogram
    Router can have only one parent — but every cover needs its own Dispatcher
    with its own router tree. Handler callbacks stay shared (so do module-level
    helpers/state); only the router objects are new.
    """
    clone = Router(name=template.name)
    for event_name, observer in template.observers.items():
        target = clone.observers[event_name]
        target.handlers = [copy.copy(handler) for handler in observer.handlers]
        target._handler.filters = list(observer._handler.filters or [])
    for sub in template.sub_routers:
        clone.include_router(_clone_router(sub))
    return clone


def build_router() -> Router:
    """A NEW router tree on every call (one per cover dispatcher)."""
    root = Router(name="root")
    # Specific callback routers must precede the catch-all text router.
    for module in (
        ban,
        start,
        profile,
        article,
        mystery,
        payment,
        personal,
        dialog,
        support,
        text,
    ):
        root.include_router(_clone_router(module.router))
    return root
