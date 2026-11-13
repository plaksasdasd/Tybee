"""Registry of running covers: cover_id -> Bot (+ metadata).

Filled and emptied by the supervisor. Everything that must talk to a client
(outbox, notifications) asks the registry for the bot of the right cover.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from aiogram import Bot


@dataclass
class RunningCover:
    cover_id: int
    slug: str
    bot: Bot


# Slug of the cover handling the current update/task; shown in every log line.
cover_slug_var: ContextVar[str] = ContextVar("cover_slug", default="-")

_by_id: dict[int, RunningCover] = {}


def register(cover_id: int, slug: str, bot: Bot) -> None:
    _by_id[cover_id] = RunningCover(cover_id, slug, bot)


def unregister(cover_id: int) -> RunningCover | None:
    return _by_id.pop(cover_id, None)


def get_bot(cover_id: int | None) -> Bot | None:
    if cover_id is None:
        return None
    running = _by_id.get(cover_id)
    return running.bot if running else None


def get_by_slug(slug: str) -> RunningCover | None:
    for running in _by_id.values():
        if running.slug == slug:
            return running
    return None


def is_running(cover_id: int) -> bool:
    return cover_id in _by_id


def running() -> list[RunningCover]:
    return list(_by_id.values())


def clear() -> None:
    _by_id.clear()
