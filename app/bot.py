from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Dispatcher

from app.avito_webhook import start_web_server
from app.config import get_settings
from app.covers import registry
from app.covers.loader import init_default_cover
from app.covers.service import upgrade_all_covers
from app.covers.supervisor import Supervisor
from app.db.base import init_models, migrate_avito_profiles, session_factory
from app.db.seed import reset_admin_states
from app.handlers import build_router
from app.middlewares import DbSessionMiddleware
from app.services.avito import close_avito
from app.services.broadcast_worker import broadcast_worker
from app.services.outbox import outbox_worker

logger = logging.getLogger(__name__)


class _CoverLogFilter(logging.Filter):
    """Adds the slug of the cover being served to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.cover = registry.cover_slug_var.get()
        return True


def create_dispatcher(cover_id: int) -> Dispatcher:
    """A NEW dispatcher and router tree per cover (a router has one parent only);
    the covers share nothing but the database."""
    dp = Dispatcher()
    middleware = DbSessionMiddleware(cover_id)
    # Outer (not inner) middlewares run for EVERY update, before filters —
    # required here: the ban guard and the per-update user must not depend on
    # some handler's filters matching first.
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    dp.include_router(build_router())
    return dp


def _setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.addFilter(_CoverLogFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | [%(cover)s] %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


async def run() -> None:
    _setup_logging()
    await init_models()
    await migrate_avito_profiles()
    await reset_admin_states()
    if init_default_cover():
        logger.info("Created %s/default from the bundled template", get_settings().covers_root)
    # New template keys (e.g. statuses added in ТЗ-2) must exist before the validator sees the covers.
    async with session_factory() as session:
        await upgrade_all_covers(session)

    supervisor = Supervisor()
    supervisor_task = asyncio.create_task(supervisor.run(), name="covers-supervisor")
    outbox_task = asyncio.create_task(outbox_worker(), name="outbox")
    broadcast_task = asyncio.create_task(broadcast_worker(), name="broadcasts")
    webhook_runner = await start_web_server()
    try:
        await supervisor_task
    finally:
        outbox_task.cancel()
        broadcast_task.cancel()
        supervisor_task.cancel()
        for task in (outbox_task, broadcast_task, supervisor_task):
            with suppress(asyncio.CancelledError):
                await task
        await supervisor.shutdown()
        if webhook_runner is not None:
            await webhook_runner.cleanup()
        await close_avito()
