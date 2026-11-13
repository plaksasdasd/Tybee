"""Background loop: support_inbox -> support threads, then Telegram notifications; shift auto-end sweep."""
from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import select

from app.db import base as db_base
from app.db.models import Cover
from app.support import shift
from app.support.ingest import process_inbox_batch
from app.support.notify import send_notifications, send_staff_messages
from panel.audit import write_audit
from panel import support_events

logger = logging.getLogger(__name__)
SWEEP_EVERY_SEC = 30.0


async def run_once() -> int:
    async with db_base.session_factory() as session:
        notes = await process_inbox_batch(session)
        await session.commit()
        if session.info.get("support_changed"):
            support_events.publish("support")
        slugs = {}
        if notes:
            slugs = dict((await session.execute(select(Cover.id, Cover.slug))).all())
    await send_notifications(notes, slugs)
    return len(notes)


async def sweep_shifts() -> list[shift.AutoEnd]:
    async with db_base.session_factory() as session:
        ended = await shift.sweep(session)
        for e in ended:
            target = str(e.staff.telegram_id)
            await write_audit(session, e.staff, "support.shift", target, {"on_shift": False, "reason": e.reason})
            if e.released:
                await write_audit(session, e.staff, "support.release", target, {"threads": e.released, "reason": e.reason})
        await session.commit()
    if ended:
        support_events.publish("support")
        await send_staff_messages([
            (
                e.staff.telegram_id,
                f"⏹ Смена завершена автоматически: {shift.REASON_LABELS[e.reason]}."
                + (f"\nОжидающие ответа диалоги ({e.released}) возвращены в очередь." if e.released else "")
                + "\nЧтобы продолжить, начните смену в панели заново.",
            )
            for e in ended
        ])
    return ended


async def run_support_worker(interval: float = 0.25) -> None:
    next_sweep = 0.0
    while True:
        try:
            handled = await run_once()
            if time.monotonic() >= next_sweep:
                next_sweep = time.monotonic() + SWEEP_EVERY_SEC
                await sweep_shifts()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("support worker iteration failed")
            await asyncio.sleep(1.0)
            continue
        if not handled:
            await asyncio.sleep(interval)
