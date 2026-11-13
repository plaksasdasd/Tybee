"""One-off import of the old support bot history (``tybee_support``) into web-panel threads.

    python -m panel.import_support --host db --port 5432 --db tybee_support --user taibi --password ... [--dry-run]
    python -m panel.import_support --url postgresql+asyncpg://user:pass@host:5432/tybee_support

The old support bot has been removed from the repository; its database is read with raw SQL,
so only its tables are needed. Settings of the old database are passed as arguments
(``--password`` may be omitted and taken from the TYBEE_SUPPORT_DB_PASSWORD variable so it does
not end up in the shell history). Idempotent: a second run adds nothing."""
from __future__ import annotations

import argparse
import asyncio
import os
from urllib.parse import quote
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.db import base as db_base
from app.db.models import Staff, StaffRole, SupportThread, SupportThreadMessage, User


@dataclass
class Stats:
    threads_created: int = 0
    threads_existing: int = 0
    messages: int = 0
    skipped_requests: int = 0
    staff_created: int = 0
    skipped_users: set[int] = field(default_factory=set)

    def __str__(self) -> str:
        return (
            f"threads: +{self.threads_created} (existing {self.threads_existing}), messages: +{self.messages}, "
            f"requests skipped (no such client): {self.skipped_requests}, staff created: {self.staff_created}"
        )


def _aware(value: datetime | str | None) -> datetime | None:
    if isinstance(value, str):  # raw SQL on SQLite returns text
        value = datetime.fromisoformat(value)
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def import_history(session: AsyncSession, support: AsyncEngine, dry_run: bool = False) -> Stats:
    stats = Stats()
    async with support.connect() as conn:
        old_staff = (await conn.execute(text("SELECT telegram_id, display_name, is_active FROM staff"))).all()
        requests = (await conn.execute(text(
            "SELECT id, customer_id, source, channel, status, assigned_to, created_at FROM support_requests ORDER BY id"
        ))).mappings().all()
        messages = (await conn.execute(text(
            "SELECT request_id, sender_id, sender_kind, body, source_event_id, created_at "
            "FROM support_messages ORDER BY id"
        ))).mappings().all()

    for tg_id, name, active in old_staff:
        if await session.get(Staff, tg_id) is None:
            session.add(Staff(telegram_id=tg_id, display_name=name, role=StaffRole.CLIENT_MANAGER_1.value, is_active=bool(active)))
            stats.staff_created += 1
    await session.flush()

    by_customer: dict[int, list] = {}
    for r in requests:
        by_customer.setdefault(r["customer_id"], []).append(r)
    msgs_by_request: dict[int, list] = {}
    for m in messages:
        msgs_by_request.setdefault(m["request_id"], []).append(m)

    for customer_id, reqs in by_customer.items():
        if await session.get(User, customer_id) is None:
            stats.skipped_requests += len(reqs)
            stats.skipped_users.add(customer_id)
            continue
        thread = await session.scalar(select(SupportThread).where(SupportThread.user_id == customer_id))
        if thread is None:
            assigned = next((r["assigned_to"] for r in reversed(reqs) if r["assigned_to"] is not None), None)
            if assigned is not None and await session.get(Staff, assigned) is None:
                assigned = None
            thread = SupportThread(
                user_id=customer_id,
                channel=reqs[-1]["channel"] or "telegram",
                status="open" if any(r["status"] != "closed" for r in reqs) else "closed",
                assigned_to=assigned, unread=0, created_at=_aware(reqs[0]["created_at"]),
            )
            session.add(thread)
            await session.flush()
            stats.threads_created += 1
        else:
            stats.threads_existing += 1

        existing = {
            (m.direction, m.body, _aware(m.created_at))
            for m in await session.scalars(select(SupportThreadMessage).where(SupportThreadMessage.thread_id == thread.id))
        }
        known_inbox = set(
            await session.scalars(
                select(SupportThreadMessage.inbox_id).where(
                    SupportThreadMessage.thread_id == thread.id, SupportThreadMessage.inbox_id.is_not(None)
                )
            )
        )
        batch = sorted(
            (m for r in reqs for m in msgs_by_request.get(r["id"], [])), key=lambda m: _aware(m["created_at"])
        )
        last_at = None
        for m in batch:
            direction = {"client": "client", "staff": "staff"}.get(m["sender_kind"], "system")
            at = _aware(m["created_at"])
            if m["source_event_id"] is not None and m["source_event_id"] in known_inbox:
                continue
            if (direction, m["body"], at) in existing:
                continue
            session.add(
                SupportThreadMessage(
                    thread_id=thread.id, direction=direction,
                    staff_id=m["sender_id"] if direction == "staff" else None,
                    body=m["body"], kind="message" if direction != "system" else "status",
                    inbox_id=m["source_event_id"], created_at=at,
                )
            )
            existing.add((direction, m["body"], at))
            stats.messages += 1
            last_at = at
        if last_at and (thread.last_message_at is None or _aware(thread.last_message_at) < last_at):
            thread.last_message_at = last_at
        await session.flush()

    if dry_run:
        await session.rollback()
    else:
        await session.commit()
    return stats


def support_url(args: argparse.Namespace) -> str:
    if args.url:
        return args.url
    password = args.password if args.password is not None else os.getenv("TYBEE_SUPPORT_DB_PASSWORD", "")
    return (
        f"postgresql+asyncpg://{quote(args.user, safe='')}:{quote(password, safe='')}"
        f"@{args.host}:{args.port}/{args.db}"
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m panel.import_support", description=__doc__.split("\n")[0])
    ap.add_argument("--url", help="SQLAlchemy URL of the old support database (overrides the parts below)")
    ap.add_argument("--host", default="db")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--db", default="tybee_support")
    ap.add_argument("--user", default="taibi")
    ap.add_argument("--password", default=None, help="default: $TYBEE_SUPPORT_DB_PASSWORD")
    ap.add_argument("--dry-run", action="store_true")
    return ap


async def _main(args: argparse.Namespace) -> None:
    await db_base.init_models()
    engine = create_async_engine(support_url(args))
    try:
        async with db_base.session_factory() as session:
            print(await import_history(session, engine, args.dry_run))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main(build_parser().parse_args()))
