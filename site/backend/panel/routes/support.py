from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
import logging
import base64
import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile
from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AvitoChat,
    AvitoProfile,
    Cover,
    Order,
    Staff,
    StaffRole,
    StaffShift,
    SupportAttachment,
    SupportInbox,
    SupportOutbox,
    SupportTemplate,
    SupportThread,
    SupportThreadMessage,
    User,
    UserCover,
)
from app.support import actions, assign, shift
from app.support import attachments as files
from app.services.notify import resolve_dialog_cover
from app.support.common import FINAL_STATUSES, NO_PING_STATUSES, ORDER_STATUS_LABELS, ORDER_STATUSES, SETTABLE, SupportError, now, ts
from panel import idempotency
from panel.audit import write_audit
from panel.auth import csrf_guard, current_staff, require_perm
from panel.deps import get_session
from panel.permissions import Perm, perms_of

router = APIRouter(prefix="/api", tags=["support"], dependencies=[Depends(csrf_guard)])


def _responsible(thread: SupportThread, me: Staff) -> bool:
    return thread.assigned_to == me.telegram_id or me.role == StaffRole.ADMIN.value


def _iso(value: datetime | None) -> str | None:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat() if value else None


async def _get_thread(session: AsyncSession, thread_id: int, me: Staff, *, responsible: bool = False) -> SupportThread:
    thread = await session.scalar(select(SupportThread).where(SupportThread.id == thread_id))
    if thread is None:
        raise HTTPException(404, "Ветка не найдена")
    if responsible:
        if not _responsible(thread, me):
            raise HTTPException(403, "Ветка закреплена за другим сотрудником" if thread.assigned_to else "Сначала возьмите ветку в работу")
    elif not (
        thread.assigned_to in (None, me.telegram_id) or Perm.SUPPORT_VIEW_ALL in perms_of(me)
    ):
        raise HTTPException(403, "Это чужая ветка")
    return thread


def _wrap(exc: SupportError) -> HTTPException:
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return HTTPException(exc.status, str(exc), headers=headers)


def _require_shift(me: Staff) -> None:
    """Starting or continuing work needs an active shift.

    The panel turns ``shift_required`` into the "Начать смену?" dialog and repeats
    the same request with the same ``Idempotency-Key``."""
    if not me.on_shift:
        raise HTTPException(409, "Сначала начните смену", headers={"X-Tybee-Reason": "shift_required"})


async def _avito_titles(session: AsyncSession, chat_refs: set[int]) -> dict[int, str]:
    """avito_chats.id -> "Avito · {profile title}"."""
    if not chat_refs:
        return {}
    rows = await session.execute(
        select(AvitoChat.id, AvitoProfile.title, AvitoProfile.slug)
        .join(AvitoProfile, AvitoProfile.id == AvitoChat.profile_id)
        .where(AvitoChat.id.in_(chat_refs))
    )
    return {cid: f"Avito · {title or slug}" for cid, title, slug in rows.all()}


ORDER_TYPE_LABELS = {"article": "По артикулу", "personal": "Персональный", "mystery_box": "Mystery Box"}


async def _client_orders(
    session: AsyncSession, user_ids: set[int], *, operations: bool = False
) -> dict[int, dict[str, Any]]:
    """Per client: the newest order in full (shown in the list preview) and the few before it."""
    if not user_ids:
        return {}
    ranked = (
        select(
            Order.id, Order.user_id, Order.title, Order.article, Order.type, Order.status, Order.price,
            Order.description, Order.created_at, Order.updated_at, Order.last_ping_at,
            Order.paid_amount, Order.paid_at, Order.refund_amount, Order.refunded_at,
            func.count().over(partition_by=Order.user_id).label("total"),
            func.row_number().over(
                partition_by=Order.user_id, order_by=(Order.created_at.desc(), Order.id.desc())
            ).label("rn"),
        )
        .where(Order.user_id.in_(user_ids))
        .subquery()
    )
    rows = (
        await session.execute(select(ranked).where(ranked.c.rn <= 4).order_by(ranked.c.user_id, ranked.c.rn))
    ).all()
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        entry = out.setdefault(int(row.user_id), {"total": int(row.total), "last": None, "others": []})
        brief = {
            "id": row.id, "title": row.title, "status": row.status,
            "status_label": ORDER_STATUS_LABELS.get(row.status, row.status),
            "price": row.price, "created_at": _iso(row.created_at),
        }
        if row.rn == 1:
            entry["last"] = {
                **brief,
                "article": row.article,
                "type": row.type, "type_label": ORDER_TYPE_LABELS.get(row.type, row.type),
                "description": (row.description or "").strip()[:600] or None,
                "updated_at": _iso(row.updated_at),
                "last_ping_at": _iso(row.last_ping_at),
                # Payment and refund figures follow the same rule as the order drawer.
                "operations": (
                    {"paid_amount": row.paid_amount, "paid_at": _iso(row.paid_at),
                     "refund_amount": row.refund_amount, "refunded_at": _iso(row.refunded_at)}
                    if operations else None
                ),
            }
        else:
            entry["others"].append(brief)
    return out


async def _thread_rows(
    session: AsyncSession, threads: list[SupportThread], me: Staff | None = None
) -> list[dict[str, Any]]:
    if not threads:
        return []
    avito = await _avito_titles(session, {t.last_avito_chat_ref for t in threads if t.last_avito_chat_ref})
    users = {u.id: u for u in await session.scalars(select(User).where(User.id.in_({t.user_id for t in threads})))}
    client_orders = await _client_orders(
        session, {t.user_id for t in threads},
        operations=bool(me and Perm.ORDERS_OPERATIONS_VIEW in perms_of(me)),
    )
    staff_ids = {t.assigned_to for t in threads if t.assigned_to is not None}
    names = {}
    if staff_ids:
        names = dict((await session.execute(select(Staff.telegram_id, Staff.display_name).where(Staff.telegram_id.in_(staff_ids)))).all())
    cover_ids = {t.last_cover_id for t in threads if t.last_cover_id}
    slugs = {}
    if cover_ids:
        slugs = dict((await session.execute(select(Cover.id, Cover.slug).where(Cover.id.in_(cover_ids)))).all())
    last = {}
    previews: dict[int, tuple[str, int | None]] = {}
    for t in threads:
        row = (await session.execute(
            select(SupportThreadMessage.body, SupportThreadMessage.cover_id)
            .where(SupportThreadMessage.thread_id == t.id)
            .order_by(SupportThreadMessage.id.desc()).limit(1)
        )).first()
        previews[t.id] = ((row[0] or ""), row[1]) if row else ("", None)
    preview_cover_ids = {cover_id for _, cover_id in previews.values() if cover_id}
    if preview_cover_ids:
        slugs.update(dict((await session.execute(
            select(Cover.id, Cover.slug).where(Cover.id.in_(preview_cover_ids))
        )).all()))
    for t in threads:
        body, cover_id = previews[t.id]
        last[t.id] = _strip_cover_prefix(body, slugs.get(cover_id))[:120]
    out = []
    for t in threads:
        u = users.get(t.user_id)
        out.append({
            "id": t.id,
            "client": {
                "id": t.user_id, "name": (u.full_name if u else "") or str(t.user_id),
                "username": u.username if u else None, "is_banned": bool(u and u.is_banned),
            },
            "assigned_to": t.assigned_to, "assigned_name": names.get(t.assigned_to),
            "route_key": f"{t.channel}:{t.last_avito_chat_ref if t.channel == 'avito' else t.last_cover_id}",
            "needs_reply": t.needs_reply, "waiting_since": _iso(t.waiting_since),
            "handoff_note": t.handoff_note, "next_step": t.next_step, "due_at": _iso(t.due_at),
            "status": t.status, "channel": t.channel,
            "channel_label": avito.get(t.last_avito_chat_ref) or ("Avito" if t.channel == "avito" else None),
            "cover": slugs.get(t.last_cover_id), "unread": t.unread or 0,
            "last_message": last[t.id], "last_message_at": _iso(t.last_message_at),
            "client_orders": client_orders.get(t.user_id) or {"total": 0, "last": None},
        })
    return out


CLIENT_SEARCH_LIMIT = 20


async def _client_hits(session: AsyncSession, rows: list[Any]) -> list[dict[str, Any]]:
    """Enrich search rows with covers, order count and the existing thread."""
    ids = [int(row.id) for row in rows]
    if not ids:
        return []
    covers: dict[int, list[str]] = {}
    for user_id, slug in (
        await session.execute(
            select(UserCover.user_id, Cover.slug)
            .join(Cover, Cover.id == UserCover.cover_id)
            .where(UserCover.user_id.in_(ids), Cover.enabled.is_(True), Cover.archived.is_(False))
            .order_by(UserCover.last_seen_at.desc())
        )
    ).all():
        covers.setdefault(int(user_id), []).append(slug)
    counts = dict(
        (
            await session.execute(
                select(Order.user_id, func.count()).where(Order.user_id.in_(ids)).group_by(Order.user_id)
            )
        ).all()
    )
    threads = {
        t.user_id: t
        for t in await session.scalars(select(SupportThread).where(SupportThread.user_id.in_(ids)))
    }
    names: dict[int, str] = {}
    staff_ids = {t.assigned_to for t in threads.values() if t.assigned_to is not None}
    if staff_ids:
        names = dict(
            (
                await session.execute(
                    select(Staff.telegram_id, Staff.display_name).where(Staff.telegram_id.in_(staff_ids))
                )
            ).all()
        )
    out: list[dict[str, Any]] = []
    for row in rows:
        user_id = int(row.id)
        thread = threads.get(user_id)
        out.append({
            "id": user_id,
            "name": row.full_name or str(user_id),
            "username": row.username,
            "is_banned": bool(row.is_banned),
            "last_seen_at": _iso(row.last_seen_at),
            "covers": covers.get(user_id, []),
            "orders_count": int(counts.get(user_id, 0)),
            "thread": {
                "id": thread.id,
                "status": thread.status,
                "assigned_to": thread.assigned_to,
                "assigned_name": names.get(thread.assigned_to),
                "needs_reply": bool(thread.needs_reply),
            } if thread is not None else None,
        })
    return out


@router.get("/support/clients")
async def search_clients(
    q: str = Query("", max_length=120),
    _: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)),
    session: AsyncSession = Depends(get_session),
):
    """Find any Telegram client who has a live bot, not only those with a thread."""
    term = " ".join(q.split()).lstrip("@").strip()
    if not term:
        raise HTTPException(422, "Введите имя, @username, ID клиента или номер заказа")
    last_seen = (
        select(func.max(UserCover.last_seen_at))
        .join(Cover, Cover.id == UserCover.cover_id)
        .where(UserCover.user_id == User.id, Cover.enabled.is_(True), Cover.archived.is_(False))
        .correlate(User)
        .scalar_subquery()
    )
    columns = (User.id, User.full_name, User.username, User.is_banned, last_seen.label("last_seen_at"))
    if term.startswith("#") and term[1:].isdigit():
        order_user = await session.scalar(select(Order.user_id).where(Order.id == int(term[1:])))
        if order_user is None:
            return {"items": [], "total": 0}
        stmt = select(*columns).where(
            User.id == order_user, User.source == "telegram", last_seen.is_not(None)
        )
    elif term.isdigit() and len(term) >= 5:
        stmt = select(*columns).where(
            User.id == int(term), User.source == "telegram", last_seen.is_not(None)
        )
    else:
        if len(term) < 2:
            raise HTTPException(422, "Введите хотя бы 2 символа")
        needle = term.lower()
        rank = case(
            (func.lower(func.coalesce(User.username, "")) == needle, 1),
            (func.lower(User.full_name) == needle, 2),
            (func.lower(func.coalesce(User.username, "")).like(f"{needle}%"), 3),
            (
                or_(
                    func.lower(User.full_name).like(f"{needle}%"),
                    func.lower(User.full_name).like(f"% {needle}%"),
                ),
                4,
            ),
            else_=5,
        )
        matched = (
            select(*columns, rank.label("rank"))
            .where(
                User.source == "telegram",
                or_(
                    func.lower(func.coalesce(User.username, "")).like(f"%{needle}%"),
                    func.lower(User.full_name).like(f"%{needle}%"),
                ),
            )
            .subquery()
        )
        # Filtering and ordering on the derived table keeps the correlated
        # "last seen" subquery evaluated once per client.
        stmt = (
            select(matched)
            .where(matched.c.last_seen_at.is_not(None))
            .order_by(matched.c.rank, matched.c.last_seen_at.desc(), matched.c.id.desc())
        )
    rows = (await session.execute(stmt.limit(CLIENT_SEARCH_LIMIT))).all()
    items = await _client_hits(session, list(rows))
    return {"items": items, "total": len(items)}


@router.get("/support/threads")
async def list_threads(
    scope: str = Query("mine", pattern="^(mine|queue|all|waiting)$"),
    status: str | None = Query(None, pattern="^(open|closed)$"),
    q: str | None = None,
    after: datetime | None = None,
    cursor: str | None = None,
    paginated: bool = False,
    limit: int = Query(100, ge=1, le=200),
    me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)),
    session: AsyncSession = Depends(get_session),
):
    if scope == "all" and Perm.SUPPORT_VIEW_ALL not in perms_of(me):
        raise HTTPException(403, "Недостаточно прав")
    ordering = SupportThread.waiting_since if scope == "waiting" else SupportThread.last_message_at
    stamp = func.coalesce(ordering, SupportThread.created_at)
    stmt = select(SupportThread)
    if scope in ("mine", "waiting"):
        stmt = stmt.where(SupportThread.assigned_to == me.telegram_id)
    elif scope == "queue":
        stmt = stmt.where(SupportThread.assigned_to.is_(None), SupportThread.status == "open")
    if scope == "waiting":
        stmt = stmt.where(SupportThread.needs_reply.is_(True), SupportThread.status == "open")
    if status:
        stmt = stmt.where(SupportThread.status == status)
    if after:
        stmt = stmt.where(SupportThread.last_message_at > after)
    if q and q.strip():
        like = f"%{q.strip().lower().lstrip('@')}%"
        cond = [func.lower(User.full_name).like(like), func.lower(func.coalesce(User.username, "")).like(like)]
        if q.strip().isdigit():
            cond.append(User.id == int(q.strip()))
        stmt = stmt.join(User, User.id == SupportThread.user_id).where(or_(*cond))
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    if cursor:
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor))
            at, row_id = datetime.fromisoformat(value[0]), int(value[1])
        except (ValueError, TypeError, IndexError, KeyError):
            raise HTTPException(422, "Неверный курсор списка") from None
        compare = stamp > at if scope == "waiting" else stamp < at
        stmt = stmt.where(or_(compare, and_(stamp == at, SupportThread.id > row_id if scope == "waiting" else SupportThread.id < row_id)))
    stmt = stmt.order_by(stamp.asc() if scope == "waiting" else stamp.desc(), SupportThread.id.asc() if scope == "waiting" else SupportThread.id.desc())
    threads = list(await session.scalars(stmt.limit(limit + 1)))
    more = len(threads) > limit
    threads = threads[:limit]
    items = await _thread_rows(session, threads, me)
    if not paginated:
        return items
    next_cursor = None
    if more and threads:
        last = threads[-1]
        at = (last.waiting_since if scope == "waiting" else last.last_message_at) or last.created_at
        next_cursor = base64.urlsafe_b64encode(json.dumps([at.isoformat(), last.id]).encode()).decode()
    return {"items": items, "next_cursor": next_cursor, "total": total}


@router.get("/support/summary")
async def summary(me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)):
    unread = await session.scalar(
        select(func.coalesce(func.sum(SupportThread.unread), 0)).where(SupportThread.assigned_to == me.telegram_id)
    )
    queue = await session.scalar(
        select(func.count()).select_from(SupportThread).where(SupportThread.assigned_to.is_(None), SupportThread.status == "open")
    )
    waiting = await session.scalar(select(func.count()).select_from(SupportThread).where(
        SupportThread.assigned_to == me.telegram_id, SupportThread.status == "open", SupportThread.needs_reply.is_(True)))
    return {"waiting": int(waiting or 0), "unread": int(unread or 0), "queue": int(queue or 0), "on_shift": me.on_shift,
            "shift": await _shift_state(session, me)}


async def _shift_state(session: AsyncSession, me: Staff) -> dict[str, Any]:
    """Current shift start, or why the last one ended (shown when the system ended it)."""
    last = await shift.last_shift(session, me.telegram_id)
    running = me.on_shift and last is not None and last.ended_at is None
    auto = (not running and last is not None and last.end_reason in shift.AUTO_REASONS
            and ts(now()) - ts(last.ended_at) < 12 * 3600)
    return {
        "started_at": _iso(last.started_at) if running else None,
        "elapsed_sec": int(ts(now()) - ts(last.started_at)) if running else 0,
        "auto_ended": {"reason": last.end_reason, "label": shift.REASON_LABELS[last.end_reason],
                       "at": _iso(last.ended_at)} if auto else None,
        "idle_after_sec": int(shift.IDLE_AFTER.total_seconds()),
    }


@router.get("/support/agents")
async def agents(
    _: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)
):
    rows = []
    for s in await session.scalars(select(Staff).where(Staff.is_active.is_(True)).order_by(Staff.display_name)):
        if Perm.SUPPORT_CHAT not in perms_of(s):
            continue
        load = await session.scalar(
            select(func.count()).select_from(SupportThread).where(SupportThread.assigned_to == s.telegram_id, SupportThread.status == "open")
        )
        rows.append({"telegram_id": s.telegram_id, "display_name": s.display_name, "on_shift": s.on_shift, "open": int(load or 0)})
    return rows


@router.get("/support/threads/{thread_id}")
async def get_thread(thread_id: int, me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)):
    thread = await _get_thread(session, thread_id, me)
    row = (await _thread_rows(session, [thread], me))[0]
    covers = list(
        (await session.execute(
            select(Cover.slug, UserCover.last_seen_at).join(UserCover, UserCover.cover_id == Cover.id)
            .where(UserCover.user_id == thread.user_id).order_by(UserCover.last_seen_at.desc())
        )).all()
    )
    orders = list(
        await session.scalars(select(Order).where(Order.user_id == thread.user_id).order_by(Order.created_at.desc(), Order.id.desc()))
    )
    user = await session.get(User, thread.user_id)
    row["client"]["source"] = user.source if user else None
    row["covers"] = [c[0] for c in covers]
    row["orders"] = []
    for o in orders:
        allowed = []
        for code, label in ORDER_STATUSES:
            if code == o.status or (code == "refunded" and not o.paid_amount):
                continue
            try:
                actions.check_transition(o.status, code, responsible=_responsible(thread, me) or (code == "bought" and Perm.BUYOUT_MANAGE in perms_of(me)),
                                         can_refund=Perm.REFUNDS_MANAGE in perms_of(me))
            except SupportError:
                continue
            allowed.append({"code": code, "label": "Подтвердить выполненный возврат" if code == "refunded" else label})
        next_ping = o.last_ping_at + actions.PING_INTERVAL if o.last_ping_at else None
        row["orders"].append({
            "id": o.id, "type": o.type, "status": o.status, "title": o.title, "price": o.price,
            "created_at": _iso(o.created_at), "last_ping_at": _iso(o.last_ping_at),
            "operations": ({"paid_amount": o.paid_amount, "paid_at": _iso(o.paid_at), "refund_amount": o.refund_amount, "refunded_at": _iso(o.refunded_at)} if Perm.ORDERS_OPERATIONS_VIEW in perms_of(me) else None),
            "next_ping_at": _iso(next_ping), "allowed_transitions": allowed,
            "can_ping": _responsible(thread, me) and o.status not in NO_PING_STATUSES and (not next_ping or ts(next_ping) <= ts(now())),
        })
    row["statuses"] = [{"code": c, "label": l} for c, l in ORDER_STATUSES if c in SETTABLE]
    row["labels"] = ORDER_STATUS_LABELS
    row["can_reply"] = _responsible(thread, me)
    row["can_refund"] = Perm.REFUNDS_MANAGE in perms_of(me)
    return row


_COVER_PREFIX = re.compile(r"^\[([a-z0-9_.-]{1,64})\]\s?")


def _strip_cover_prefix(body: str, slug: str | None) -> str:
    """Older messages kept the cover inside the text as ``[slug] …``.

    The panel shows the cover as a separate service label, so the bubble keeps
    only the client's own words.
    """
    if not slug:
        return body
    match = _COVER_PREFIX.match(body)
    return body[match.end():] if match and match.group(1) == slug else body


def _delivery(outbox: SupportOutbox | None) -> str | None:
    if outbox is None:
        return None
    if outbox.processed_at is None:
        return "pending"
    return "uncertain" if outbox.delivery_uncertain else ("failed" if outbox.last_error else "sent")


@router.get("/support/threads/{thread_id}/messages")
async def get_messages(
    thread_id: int,
    after_id: int | None = None,
    before_id: int | None = None,
    q: str | None = Query(None, max_length=200),
    me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)),
    session: AsyncSession = Depends(get_session),
):
    thread = await _get_thread(session, thread_id, me)
    stmt = select(SupportThreadMessage).where(SupportThreadMessage.thread_id == thread.id)
    if q and q.strip():
        stmt = stmt.where(func.lower(SupportThreadMessage.body).contains(q.strip().lower(), autoescape=True))
    if before_id is not None:
        stmt = stmt.where(SupportThreadMessage.id < before_id)
    if after_id is not None:
        rows = list(await session.scalars(stmt.where(SupportThreadMessage.id > after_id).order_by(SupportThreadMessage.id).limit(200)))
    else:
        rows = list(await session.scalars(stmt.order_by(SupportThreadMessage.id.desc()).limit(200)))[::-1]
    outbox_ids = [m.outbox_id for m in rows if m.outbox_id]
    outboxes = {}
    if outbox_ids:
        outboxes = {o.id: o for o in await session.scalars(select(SupportOutbox).where(SupportOutbox.id.in_(outbox_ids)))}
    names = dict((await session.execute(select(Staff.telegram_id, Staff.display_name).where(Staff.telegram_id.in_({m.staff_id for m in rows if m.staff_id})))).all()) if any(m.staff_id for m in rows) else {}
    inbox_ids = [m.inbox_id for m in rows if m.inbox_id]
    chat_of: dict[int, int] = {}
    if inbox_ids:
        chat_of = dict((await session.execute(
            select(SupportInbox.id, SupportInbox.avito_chat_ref)
            .where(SupportInbox.id.in_(inbox_ids), SupportInbox.avito_chat_ref.is_not(None))
        )).all())
    avito = await _avito_titles(session, set(chat_of.values()))
    cover_ids = {m.cover_id for m in rows if m.cover_id}
    cover_slugs: dict[int, str] = {}
    if cover_ids:
        cover_slugs = dict((await session.execute(
            select(Cover.id, Cover.slug).where(Cover.id.in_(cover_ids))
        )).all())
    by_message: dict[int, list[dict[str, Any]]] = {}
    if rows:
        for a in await session.scalars(
            select(SupportAttachment)
            .where(SupportAttachment.thread_message_id.in_([m.id for m in rows]))
            .order_by(SupportAttachment.id)
        ):
            by_message.setdefault(a.thread_message_id, []).append(_attachment_json(a))
    return [
        {
            "id": m.id, "direction": m.direction, "kind": m.kind,
            "body": _strip_cover_prefix(m.body, cover_slugs.get(m.cover_id)),
            "cover": cover_slugs.get(m.cover_id),
            "staff_name": names.get(m.staff_id), "order_id": m.order_id, "created_at": _iso(m.created_at),
            "delivery": _delivery(outboxes.get(m.outbox_id)),
            "can_retry": bool(m.outbox_id and outboxes.get(m.outbox_id) and outboxes[m.outbox_id].processed_at and outboxes[m.outbox_id].last_error),
            "error": (outboxes[m.outbox_id].last_error if m.outbox_id in outboxes else None),
            "attachments": by_message.get(m.id, []),
            "channel_label": avito.get(chat_of.get(m.inbox_id)) if m.inbox_id else None,
        }
        for m in rows
    ]


def _attachment_json(a: SupportAttachment) -> dict[str, Any]:
    return {
        "id": a.id, "kind": a.kind, "mime": a.mime, "name": a.original_name, "size": a.size,
        "is_image": a.mime in files.INLINE_MIMES, "url": f"/api/support/attachments/{a.id}",
        "ephemeral": a.ephemeral, "direction": a.direction, "available": bool(a.remote_provider and a.remote_id) or files.resolve(a.path) is not None,
    }


@router.get("/support/attachments/{attachment_id}")
async def get_attachment(
    attachment_id: int, me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)
):
    row = await session.get(SupportAttachment, attachment_id)
    message = await session.get(SupportThreadMessage, row.thread_message_id) if row and row.thread_message_id else None
    if row is None or message is None:
        raise HTTPException(404, "Файл не найден")
    await _get_thread(session, message.thread_id, me)  # same rights as the thread itself
    if row.remote_provider and row.remote_id:
        return await _remote_attachment(session, row)
    path = files.resolve(row.path)  # only the DB path, and only inside DATA_DIR
    if path is None:
        raise HTTPException(404, "Файл не найден")
    inline = row.mime in files.INLINE_MIMES
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    if not (row.ephemeral and row.direction == "in"):
        return FileResponse(path, media_type=row.mime if inline else "application/octet-stream",
            filename=row.original_name or path.name, content_disposition_type="inline" if inline else "attachment",
            headers=headers)
    # Ignore Range: the file is removed only when the entire response body was sent.
    template = FileResponse(path, media_type=row.mime if inline else "application/octet-stream",
        filename=row.original_name or path.name, content_disposition_type="inline" if inline else "attachment",
        headers=headers)
    headers = dict(template.headers)
    headers["content-length"] = str(path.stat().st_size)
    async def stream_once():
        complete = False
        try:
            with path.open("rb") as handle:
                while chunk := await asyncio.to_thread(handle.read, 65536):
                    yield chunk
            complete = True
        finally:
            if complete:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logging.getLogger(__name__).exception("Could not remove received attachment %s", row.id)
    return StreamingResponse(stream_once(), headers=headers, media_type=row.mime if inline else "application/octet-stream")


class _LimitedBuffer:
    """File-like sink which enforces a cap even if a provider omits file_size."""
    def __init__(self, limit: int):
        import io
        self.buffer = io.BytesIO()
        self.limit = limit

    def write(self, chunk: bytes) -> int:
        if self.buffer.tell() + len(chunk) > self.limit:
            raise ValueError("файл больше 20 МБ")
        return self.buffer.write(chunk)

    def flush(self):
        self.buffer.flush()

    def seek(self, *args):
        return self.buffer.seek(*args)

    def getvalue(self) -> bytes:
        return self.buffer.getvalue()


async def _remote_attachment(session: AsyncSession, row: SupportAttachment) -> Response:
    from urllib.parse import quote
    from app.avito_webhook import AvitoDownloadError, download_avito_image
    from app.covers.loader import get_config_from_folder, resolve_path
    from app.services.avito import client_for_profile
    from aiogram import Bot

    mime = row.mime
    try:
        if row.remote_provider == "telegram":
            if row.size > files.TELEGRAM_DOWNLOAD_LIMIT:
                raise HTTPException(413, "Файл больше 20 МБ: Telegram не позволяет скачать его через Bot API")
            cover = await session.get(Cover, row.remote_cover_id) if row.remote_cover_id else None
            if cover is None:
                raise HTTPException(404, "Бот, получивший файл, недоступен")
            token = get_config_from_folder(resolve_path(cover.path)).get("token")
            if not isinstance(token, str) or not token:
                raise HTTPException(503, "Бот, получивший файл, недоступен")
            bot = Bot(token=token)
            try:
                info = await asyncio.wait_for(bot.get_file(row.remote_id), timeout=30)
                if info.file_size and info.file_size > files.TELEGRAM_DOWNLOAD_LIMIT:
                    raise HTTPException(413, "Файл больше 20 МБ: Telegram не позволяет скачать его через Bot API")
                if not info.file_path:
                    raise HTTPException(502, "Telegram не вернул путь к файлу")
                sink = _LimitedBuffer(files.TELEGRAM_DOWNLOAD_LIMIT)
                await asyncio.wait_for(bot.download_file(info.file_path, destination=sink), timeout=120)
                data = sink.getvalue()
            finally:
                await bot.session.close()
        elif row.remote_provider in {"avito_image", "avito_voice"}:
            url = row.remote_id
            if row.remote_provider == "avito_voice":
                profile = await session.get(AvitoProfile, row.remote_profile_id) if row.remote_profile_id else None
                if profile is None:
                    raise HTTPException(404, "Профиль Avito недоступен")
                url = await client_for_profile(profile).voice_url(row.remote_id)
                if not url:
                    raise HTTPException(404, "Avito не вернул ссылку на голосовое сообщение")
            data = await download_avito_image(url)
        else:
            raise HTTPException(404, "Источник файла неизвестен")
        if not data:
            raise HTTPException(502, "Провайдер вернул пустой файл")
        if row.kind in {"image", "photo"} or mime in files.INLINE_MIMES:
            mime = await asyncio.to_thread(files.validate_image, data)
            if not mime:
                raise HTTPException(502, "Провайдер вернул повреждённое изображение")
    except HTTPException:
        raise
    except ValueError as exc:
        if "больше 20 МБ" in str(exc):
            raise HTTPException(413, "Файл больше 20 МБ: Telegram не позволяет скачать его через Bot API") from None
        raise HTTPException(502, "Не удалось получить файл у провайдера") from None
    except AvitoDownloadError as exc:
        if "больше" in str(exc) or "слишком большой" in str(exc):
            raise HTTPException(413, str(exc)) from None
        raise HTTPException(502, "Не удалось получить файл у Avito") from None
    except Exception as exc:
        # Provider errors can contain the bot token or a signed URL; never expose them.
        if "file is too big" in str(exc).lower():
            raise HTTPException(413, "Файл больше 20 МБ: Telegram не позволяет скачать его через Bot API") from None
        logging.getLogger(__name__).warning("Remote attachment %s failed: %s", row.id, type(exc).__name__)
        raise HTTPException(502, "Не удалось получить файл у провайдера") from None
    inline = mime in files.INLINE_MIMES
    name = files.safe_name(row.original_name, "file")
    return Response(data, media_type=mime if inline else "application/octet-stream", headers={
        "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })


TEXT_LIMIT = 3500


async def _read_reply(request: Request) -> tuple[str, UploadFile | None]:
    """``multipart/form-data`` (text, file) or legacy JSON ``{"text": ...}``."""
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            data = await request.json()
        except ValueError:
            raise HTTPException(422, "Неверный JSON") from None
        text = data.get("text") if isinstance(data, dict) else None
        return (text if isinstance(text, str) else ""), None
    form = await request.form(max_files=1, max_fields=5)
    text = form.get("text")
    upload = form.get("file")
    if isinstance(upload, UploadFile) and not upload.filename and not upload.size:
        upload = None
    return (text if isinstance(text, str) else ""), (upload if isinstance(upload, UploadFile) else None)


async def _check_upload(upload: UploadFile, avito: bool) -> files.StoredFile:
    """JPEG/PNG/WebP up to 10 MB or PDF up to 20 MB, by signature; Avito clients get images only."""
    data = await upload.read(files.REPLY_PDF_LIMIT + 1)
    if not data:
        raise HTTPException(422, "Пустой файл")
    image = files.validate_image(data)
    if image is not None:
        if len(data) > files.REPLY_IMAGE_LIMIT:
            raise HTTPException(422, "Изображение больше 10 МБ")
        kind, mime = "photo", image
    elif files.sniff_pdf(data):
        if avito:
            raise HTTPException(422, "Клиенту Avito можно отправить только изображение")
        if len(data) > files.REPLY_PDF_LIMIT:
            raise HTTPException(422, "PDF больше 20 МБ")
        kind, mime = "document", "application/pdf"
    else:
        raise HTTPException(422, "Допустимы только JPEG, PNG, WebP и PDF")
    if avito:
        kind = "image"
    name = files.safe_name(upload.filename, f"file{files.IMAGE_EXT[mime]}")
    return files.store_bytes(data, kind=kind, mime=mime, original_name=name)


@router.post("/support/clients/{user_id}/dialog", status_code=201)
async def start_client_dialog(
    user_id: int,
    request: Request,
    me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)),
    session: AsyncSession = Depends(get_session),
):
    """Start (or reopen) a dialog with any client who has a live bot, first message included."""
    request_key = request.headers.get("Idempotency-Key")
    if request_key and not re.fullmatch(r"[a-zA-Z0-9-]{16,64}", request_key):
        raise HTTPException(422, "Неверный ключ отправки")
    key = f"{me.telegram_id}:{request_key}" if request_key else None
    if key:
        previous = await session.scalar(
            select(SupportThreadMessage).where(SupportThreadMessage.request_key == key)
        )
        if previous is not None:
            if previous.direction != "staff":
                raise HTTPException(409, "Ключ уже использован в другом действии")
            return {"thread_id": previous.thread_id, "message_id": previous.id}

    # Read the body before any early refusal: an unread multipart upload would
    # reset the connection and the browser would lose the "начать смену" answer.
    text, upload = await _read_reply(request)

    me = await session.scalar(
        select(Staff)
        .where(Staff.telegram_id == me.telegram_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if me is None or not me.is_active or Perm.SUPPORT_CHAT not in perms_of(me):
        raise HTTPException(403, "Доступ к поддержке отключён")
    _require_shift(me)

    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise HTTPException(404, "Клиент не найден")
    if user.id == me.telegram_id:
        raise HTTPException(409, "Это ваш собственный аккаунт")
    if user.source != "telegram":
        raise HTTPException(
            409, "Клиенту Avito пишут из его ветки", headers={"X-Tybee-Reason": "not_telegram"}
        )
    if user.is_banned:
        raise HTTPException(
            409, "Клиент заблокирован. Сначала разблокируйте его", headers={"X-Tybee-Reason": "banned"}
        )

    thread = await session.scalar(
        select(SupportThread).where(SupportThread.user_id == user.id).with_for_update()
    )
    if thread is not None and thread.assigned_to not in (None, me.telegram_id):
        owner = await session.get(Staff, thread.assigned_to)
        raise HTTPException(
            409,
            f"Диалог ведёт {owner.display_name if owner is not None else 'другой сотрудник'}",
            headers={"X-Tybee-Reason": "thread_owned"},
        )
    if thread is not None and thread.channel == "avito":
        raise HTTPException(
            409, "Клиент пишет через Avito: откройте его ветку", headers={"X-Tybee-Reason": "not_telegram"}
        )
    cover_id = await resolve_dialog_cover(
        session, user.id, thread_cover_id=thread.last_cover_id if thread is not None else None
    )
    if cover_id is None:
        raise HTTPException(
            409,
            "Клиент не начинал диалог с ботом: писать ему нельзя",
            headers={"X-Tybee-Reason": "no_cover"},
        )

    text = text.strip()
    if re.search(r"\{(?:client_name|order_id|order_title|order_price|order_status)\}", text):
        raise HTTPException(422, "Заполните переменные шаблона")
    if len(text) > TEXT_LIMIT:
        raise HTTPException(422, f"Сообщение длиннее {TEXT_LIMIT} символов")
    if not text and upload is None:
        raise HTTPException(422, "Пустое сообщение")
    stored = await _check_upload(upload, avito=False) if upload is not None else None
    try:
        thread, msg = await actions.start_dialog(session, user, me, text, stored, cover_id=cover_id)
        msg.request_key = key
        details: dict[str, Any] = {"thread_id": thread.id, "client_id": user.id}
        if stored is not None:
            details.update(file=stored.original_name, size=stored.size, mime=stored.mime)
        await write_audit(session, me, "support.initiate", str(thread.id), details)
        await session.commit()
    except SupportError as exc:
        await session.rollback()
        if stored is not None:
            files.discard([stored])
        raise _wrap(exc) from None
    except BaseException:
        if stored is not None:
            files.discard([stored])
        raise
    return {"thread_id": thread.id, "message_id": msg.id}


@router.post("/support/threads/{thread_id}/reply", status_code=201)
async def reply(thread_id: int, request: Request, me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)):
    thread = await _get_thread(session, thread_id, me, responsible=True)
    thread = await session.scalar(select(SupportThread).where(SupportThread.id == thread_id).with_for_update().execution_options(populate_existing=True))
    if not _responsible(thread, me):
        raise HTTPException(409, "Ответственный изменился. Обновите диалог")
    request_key = request.headers.get("Idempotency-Key")
    if request_key and not re.fullmatch(r"[a-zA-Z0-9-]{16,64}", request_key):
        raise HTTPException(422, "Неверный ключ отправки")
    key = f"{me.telegram_id}:{request_key}" if request_key else None
    if key:
        previous = await session.scalar(select(SupportThreadMessage).where(SupportThreadMessage.request_key == key))
        if previous:
            if previous.thread_id != thread_id or previous.direction != "staff":
                raise HTTPException(409, "Ключ уже использован в другом диалоге")
            return {"id": previous.id}
    # Checked after the idempotency lookup: a retry of an already delivered
    # message must not fail just because the shift ended in between.
    # The body is read first so an unread upload cannot reset the connection
    # and hide the "начать смену" answer from the browser.
    text, upload = await _read_reply(request)
    _require_shift(me)
    expected_route = request.headers.get("X-Support-Route")
    route = f"{thread.channel}:{thread.last_avito_chat_ref if thread.channel == 'avito' else thread.last_cover_id}"
    if expected_route and expected_route != route:
        raise HTTPException(409, "Канал ответа изменился. Обновите диалог; прежний черновик сохранён для его канала")
    text = text.strip()
    if re.search(r"\{(?:client_name|order_id|order_title|order_price|order_status)\}", text):
        raise HTTPException(422, "Заполните переменные шаблона")
    if len(text) > TEXT_LIMIT:
        raise HTTPException(422, f"Сообщение длиннее {TEXT_LIMIT} символов")
    if not text and upload is None:
        raise HTTPException(422, "Пустое сообщение")
    stored = None
    if upload is not None:
        user = await session.get(User, thread.user_id)
        stored = await _check_upload(upload, avito=bool(user and user.source == "avito"))
    try:
        msg = await actions.reply(session, thread, me, text, stored)
        msg.request_key = key
        details: dict[str, Any] = {"thread_id": thread.id}
        if stored is not None:
            details.update(file=stored.original_name, size=stored.size, mime=stored.mime)
        await write_audit(session, me, "support.reply", str(thread.id), details)
        await session.commit()
    except BaseException:
        if stored is not None:
            files.discard([stored])
        raise
    return {"id": msg.id}


@router.post("/support/threads/{thread_id}/claim")
async def claim(thread_id: int, me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)):
    thread = await session.get(SupportThread, thread_id)
    if thread is None:
        raise HTTPException(404, "Ветка не найдена")
    me = await session.scalar(select(Staff).where(Staff.telegram_id == me.telegram_id).with_for_update().execution_options(populate_existing=True))
    if me is None or not me.is_active or Perm.SUPPORT_CHAT not in perms_of(me):
        raise HTTPException(403, "Доступ к поддержке отключён")
    _require_shift(me)
    if not await assign.claim(session, thread_id, me):
        await session.rollback()
        raise HTTPException(409, "Ветку уже взял другой сотрудник")
    await write_audit(session, me, "support.claim", str(thread_id))
    await session.commit()
    return {"ok": True}


class TransferBody(BaseModel):
    staff_id: int
    summary: str = Field(min_length=1, max_length=2000)
    next_step: str = Field(min_length=1, max_length=1000)
    due_at: datetime | None = None


@router.post("/support/threads/{thread_id}/transfer")
async def transfer(thread_id: int, body: TransferBody, me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)):
    thread = await _get_thread(session, thread_id, me)
    thread = await session.scalar(select(SupportThread).where(SupportThread.id == thread_id).with_for_update().execution_options(populate_existing=True))
    if Perm.SUPPORT_ASSIGN not in perms_of(me) and thread.assigned_to != me.telegram_id:
        raise HTTPException(403, "Передать диалог может его ответственный")
    if thread is None:
        raise HTTPException(404, "Ветка не найдена")
    target = await session.get(Staff, body.staff_id)
    if target is None or Perm.SUPPORT_CHAT not in perms_of(target):
        raise HTTPException(422, "Сотрудник не может вести поддержку")
    if not body.summary.strip() or not body.next_step.strip():
        raise HTTPException(422, "Заполните контекст передачи и следующий шаг")
    if not target.on_shift:
        raise HTTPException(409, "Получатель вне смены. Выберите доступного сотрудника")
    if body.due_at and body.due_at.tzinfo is None:
        raise HTTPException(422, "Укажите часовой пояс срока")
    thread.handoff_note = body.summary.strip() or None
    thread.next_step = body.next_step.strip() or None
    thread.due_at = body.due_at
    try:
        await assign.transfer(session, thread, target, me)
        session.add(SupportThreadMessage(thread_id=thread.id, direction="internal", kind="handoff", staff_id=me.telegram_id,
            body=f"Контекст передачи: {thread.handoff_note}\nСледующий шаг: {thread.next_step}" + (f"\nСрок: {_iso(thread.due_at)}" if thread.due_at else "")))
    except SupportError as exc:
        raise _wrap(exc) from None
    await write_audit(session, me, "support.transfer", str(thread_id), {"to": target.telegram_id, "summary": thread.handoff_note, "next_step": thread.next_step, "due_at": _iso(thread.due_at)})
    await session.commit()
    return {"ok": True}


@router.post("/support/threads/{thread_id}/close")
async def close(thread_id: int, me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)):
    thread = await _get_thread(session, thread_id, me, responsible=True)
    await actions.close_thread(session, thread, me)
    await write_audit(session, me, "support.close", str(thread_id))
    await session.commit()
    return {"ok": True}


class InvoiceBody(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=2000)
    price: int = Field(gt=0, le=10_000_000)


@router.post("/support/threads/{thread_id}/invoice", status_code=201)
async def invoice(thread_id: int, body: InvoiceBody, request: Request, me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT, Perm.ORDERS_INVOICE)), session: AsyncSession = Depends(get_session)):
    # Arbitration handles invoices and claims even when a client manager owns the thread.
    thread = await _get_thread(session, thread_id, me, responsible=Perm.REFUNDS_MANAGE not in perms_of(me))
    if not body.title.strip():
        raise HTTPException(422, "Укажите название счёта")
    claim, previous = await idempotency.claim(session, request, me.telegram_id, "support.invoice", {"thread_id": thread_id, **body.model_dump(mode="json")})
    if previous is not None:
        return {"order_id": previous}
    order = await actions.create_invoice(session, thread, me, body.title.strip(), body.description.strip(), body.price)
    if claim is not None:
        claim.result_id = order.id
    await write_audit(session, me, "support.invoice", str(thread_id), {"order_id": order.id, "price": body.price})
    await session.commit()
    return {"order_id": order.id}


class StatusBody(BaseModel):
    status: str
    expected_status: str | None = None
    reason: str = Field(default="", max_length=1000)
    refund_amount: int | None = Field(default=None, ge=0)
    refund_reference: str = Field(default="", max_length=500)


@router.post("/support/orders/{order_id}/status")
async def order_status(order_id: int, body: StatusBody, me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)):
    """Transitions per ТЗ-2 B.3: delivery/cancel-unpaid by the thread owner, refunds by ``refunds.manage``."""
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(404, "Заказ не найден")
    thread = await session.scalar(select(SupportThread).where(SupportThread.user_id == order.user_id))
    if thread is None:
        raise HTTPException(403, "Вы не ведёте ветку этого клиента")
    previous = order.status
    if body.expected_status is not None and body.expected_status != previous:
        raise HTTPException(409, "Статус уже изменился. Обновите карточку заказа")
    if body.status == "refunded" and Perm.REFUNDS_MANAGE not in perms_of(me):
        raise HTTPException(403, "Возвраты оформляет только агент арбитража")
    if body.status == "refunded" and (not body.refund_reference.strip() or body.refund_amount is None):
        raise HTTPException(422, "Укажите сумму и основание выполненного возврата. Эта операция не переводит деньги")
    if body.status == "refunded" and not order.paid_amount:
        raise HTTPException(409, "Нет подтверждённой суммы оплаты. Сначала проверьте платёж")
    if body.status == "refunded" and body.refund_amount != (order.paid_amount or 0):
        raise HTTPException(422, "Для полного возврата укажите подтверждённую оплаченную сумму")
    try:
        await actions.change_order_status(
            session, thread, me, order_id, body.status,
            responsible=_responsible(thread, me) or (body.status == "bought" and Perm.BUYOUT_MANAGE in perms_of(me)),
            can_refund=Perm.REFUNDS_MANAGE in perms_of(me),
        )
    except SupportError as exc:
        raise _wrap(exc) from None
    details: dict[str, Any] = {"status": body.status, "from": previous, "reason": body.reason,
                               "refund_reference": body.refund_reference}
    if body.reason.strip() or body.refund_reference.strip():
        session.add(SupportThreadMessage(thread_id=thread.id, direction="internal", kind="status_reason", staff_id=me.telegram_id,
                                        order_id=order_id, body=f"Причина: {body.reason}. Подтверждение возврата: {body.refund_reference}"))
    if body.status == "refunded":
        details["refund_amount"] = order.refund_amount
    await write_audit(session, me, "support.order.status", str(order_id), details)
    await session.commit()
    return {"ok": True}


async def _owned_order(session: AsyncSession, order_id: int, me: Staff) -> tuple[Order, SupportThread]:
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "Заказ не найден")
    thread = await session.scalar(select(SupportThread).where(SupportThread.user_id == order.user_id))
    if thread is None or not _responsible(thread, me):
        raise HTTPException(403, "Вы не ведёте ветку этого клиента")
    return order, thread


@router.post("/support/orders/{order_id}/ping")
async def order_ping(order_id: int, me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)):
    order, thread = await _owned_order(session, order_id, me)
    try:
        await actions.ping_order(session, thread, me, order)
    except SupportError as exc:
        raise _wrap(exc) from None
    await write_audit(session, me, "support.order.ping", str(order_id), {"status": order.status})
    await session.commit()
    return {"ok": True, "last_ping_at": _iso(order.last_ping_at)}


@router.get("/support/stale-orders")
async def stale_orders(
    days: int = Query(3, ge=1, le=365),
    me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)),
    session: AsyncSession = Depends(get_session),
):
    """Unfinished orders untouched for ``days`` days: of my threads (all of them with ``support.view_all``)."""
    cutoff = now() - timedelta(days=days)
    stmt = (
        select(Order, SupportThread, User)
        .join(SupportThread, SupportThread.user_id == Order.user_id)
        .join(User, User.id == Order.user_id)
        .where(Order.updated_at < cutoff, Order.status.not_in(FINAL_STATUSES))
        .order_by(Order.updated_at)
        .limit(300)
    )
    if Perm.SUPPORT_VIEW_ALL not in perms_of(me):
        stmt = stmt.where(SupportThread.assigned_to == me.telegram_id)
    rows = (await session.execute(stmt)).all()
    return [
        {
            "order_id": o.id, "title": o.title, "price": o.price, "status": o.status,
            "status_label": ORDER_STATUS_LABELS.get(o.status, o.status),
            "updated_at": _iso(o.updated_at), "last_ping_at": _iso(o.last_ping_at),
            "thread_id": t.id, "client": (u.full_name or str(u.id)), "can_ping": t.assigned_to == me.telegram_id,
        }
        for o, t, u in rows
    ]


class BanBody(BaseModel):
    banned: bool


@router.post("/support/threads/{thread_id}/ban")
async def ban(thread_id: int, body: BanBody, me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)):
    thread = await _get_thread(session, thread_id, me, responsible=True)
    try:
        await actions.set_banned(session, thread, me, body.banned)
    except SupportError as exc:
        raise _wrap(exc) from None
    await write_audit(session, me, "support.ban" if body.banned else "support.unban", str(thread_id), {"client_id": thread.user_id})
    await session.commit()
    return {"ok": True}


class ShiftBody(BaseModel):
    on_shift: bool


@router.patch("/me/shift")
async def set_shift(body: ShiftBody, me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)):
    changed = await (shift.start_shift(session, me) if body.on_shift else shift.end_shift(session, me, shift.MANUAL))
    if changed:
        await write_audit(session, me, "support.shift", str(me.telegram_id),
                          {"on_shift": body.on_shift} | ({} if body.on_shift else {"reason": shift.MANUAL}))
    await session.commit()
    return {"on_shift": me.on_shift}


class PresenceBody(BaseModel):
    active: bool = False


@router.post("/me/presence")
async def presence(body: PresenceBody, me: Staff = Depends(current_staff), session: AsyncSession = Depends(get_session)):
    """Heartbeat from an open panel tab; ``active`` = there was real mouse/keyboard input since the last one."""
    shift.touch(me, body.active)
    await session.commit()
    return {"on_shift": me.on_shift}


@router.get("/support/shifts")
async def shifts(
    days: int = Query(7, ge=1, le=62),
    staff_id: int | None = None,
    me: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    """Shift log: everyone for leads (support.assign / staff.view), otherwise only one's own."""
    perms = perms_of(me)
    see_all = Perm.SUPPORT_ASSIGN in perms or Perm.STAFF_VIEW in perms
    if not see_all and Perm.SUPPORT_CHAT not in perms:
        raise HTTPException(403, "Недостаточно прав")
    who = staff_id if see_all else me.telegram_id
    since = now() - timedelta(days=days)
    stmt = (select(StaffShift, Staff.display_name)
            .join(Staff, Staff.telegram_id == StaffShift.staff_id, isouter=True)
            .where(or_(StaffShift.started_at >= since, StaffShift.ended_at.is_(None)))
            .order_by(StaffShift.started_at.desc()).limit(500))
    if who is not None:
        stmt = stmt.where(StaffShift.staff_id == who)
    current = ts(now())
    rows, totals = [], {}
    for sh, name in (await session.execute(stmt)).all():
        dur = int((ts(sh.ended_at) if sh.ended_at else current) - ts(sh.started_at))
        rows.append({
            "id": sh.id, "staff_id": sh.staff_id, "staff_name": name or str(sh.staff_id),
            "started_at": _iso(sh.started_at), "ended_at": _iso(sh.ended_at), "duration_sec": max(dur, 0),
            "end_reason": sh.end_reason, "end_label": shift.REASON_LABELS.get(sh.end_reason or "", None),
            "auto": sh.end_reason in shift.AUTO_REASONS,
        })
        t = totals.setdefault(sh.staff_id, {"staff_id": sh.staff_id, "staff_name": name or str(sh.staff_id),
                                            "shifts": 0, "total_sec": 0, "auto_ended": 0})
        t["shifts"] += 1
        t["total_sec"] += max(dur, 0)
        t["auto_ended"] += int(sh.end_reason in shift.AUTO_REASONS)
    return {"days": days, "all": see_all and who is None, "rows": rows,
            "totals": sorted(totals.values(), key=lambda t: -t["total_sec"])}


class ReadBody(BaseModel):
    through_id: int = Field(ge=1)


@router.post("/support/threads/{thread_id}/read")
async def read_thread(thread_id: int, body: ReadBody, me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)):
    thread = await _get_thread(session, thread_id, me, responsible=True)
    thread = await session.scalar(select(SupportThread).where(SupportThread.id == thread_id).with_for_update().execution_options(populate_existing=True))
    if not _responsible(thread, me):
        raise HTTPException(409, "Ответственный изменился")
    message = await session.get(SupportThreadMessage, body.through_id)
    if not message or message.thread_id != thread_id:
        raise HTTPException(422, "Сообщение не принадлежит диалогу")
    thread.read_through_id = max(thread.read_through_id or 0, body.through_id)
    thread.unread = int(await session.scalar(select(func.count()).select_from(SupportThreadMessage).where(
        SupportThreadMessage.thread_id == thread_id, SupportThreadMessage.direction == "client",
        SupportThreadMessage.id > thread.read_through_id)) or 0)
    await session.commit()
    return {"unread": thread.unread}


class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=3500)
    next_step: str | None = Field(default=None, max_length=1000)
    due_at: datetime | None = None


@router.post("/support/threads/{thread_id}/notes", status_code=201)
async def add_note(thread_id: int, body: NoteBody, request: Request, me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)):
    thread = await _get_thread(session, thread_id, me, responsible=True)
    if not body.text.strip():
        raise HTTPException(422, "Пустая заметка")
    if body.due_at and (body.due_at.tzinfo is None or not body.next_step):
        raise HTTPException(422, "Укажите следующий шаг и часовой пояс срока")
    thread = await session.scalar(select(SupportThread).where(SupportThread.id == thread_id).with_for_update().execution_options(populate_existing=True))
    if not _responsible(thread, me):
        raise HTTPException(409, "Ответственный изменился. Обновите диалог")
    key = request.headers.get("Idempotency-Key")
    if key and not re.fullmatch(r"[a-zA-Z0-9-]{16,64}", key):
        raise HTTPException(422, "Неверный ключ заметки")
    key = f"{me.telegram_id}:{key}" if key else None
    existing = await session.scalar(select(SupportThreadMessage).where(SupportThreadMessage.request_key == key)) if key else None
    if existing:
        if existing.thread_id != thread_id or existing.direction != "internal":
            raise HTTPException(409, "Ключ уже использован")
        return {"id": existing.id}
    msg = SupportThreadMessage(thread_id=thread_id, direction="internal", kind="note", body=body.text.strip(), staff_id=me.telegram_id, request_key=key)
    session.add(msg)
    if body.next_step is not None:
        thread.next_step = body.next_step.strip() or None
        thread.due_at = body.due_at if thread.next_step else None
    await write_audit(session, me, "support.note", str(thread_id))
    await session.commit()
    return {"id": msg.id}


class RetryBody(BaseModel):
    confirm_uncertain: bool = False


@router.post("/support/threads/{thread_id}/messages/{message_id}/retry")
async def retry_message(thread_id: int, message_id: int, body: RetryBody, me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)):
    await _get_thread(session, thread_id, me, responsible=True)
    message = await session.get(SupportThreadMessage, message_id)
    if not message or message.thread_id != thread_id or message.direction != "staff" or not message.outbox_id:
        raise HTTPException(404, "Исходящее сообщение не найдено")
    out = await session.scalar(select(SupportOutbox).where(SupportOutbox.id == message.outbox_id).with_for_update())
    if not out or not out.processed_at or not out.last_error:
        raise HTTPException(409, "Сообщение уже отправлено или ожидает отправки")
    if out.delivery_uncertain and not body.confirm_uncertain:
        raise HTTPException(409, "Результат предыдущей отправки неизвестен. Повтор может создать дубликат; подтвердите повтор")
    # Reuse the same outbox/message/attachment, never create a second outgoing record.
    out.processed_at = None
    out.last_error = None
    out.delivery_uncertain = False
    out.next_attempt_at = None
    await write_audit(session, me, "support.retry", str(message_id), {"confirmed_uncertain": body.confirm_uncertain})
    await session.commit()
    return {"ok": True}


@router.get("/support/handoff")
async def handoff(me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)):
    threads = list(await session.scalars(select(SupportThread).where(
        SupportThread.assigned_to == me.telegram_id, SupportThread.status == "open",
        or_(SupportThread.needs_reply.is_(True), SupportThread.next_step.is_not(None))).order_by(SupportThread.waiting_since.asc().nulls_last(), SupportThread.id)))
    return await _thread_rows(session, threads, me)


VARIABLES = {"client_name", "order_id", "order_title", "order_price", "order_status"}


class TemplateBody(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=3500)
    is_active: bool = True


def _template(row: SupportTemplate):
    return {"id": row.id, "title": row.title, "body": row.body, "is_active": row.is_active, "updated_at": _iso(row.updated_at)}


@router.get("/support/templates")
async def templates(manage: bool = False, me: Staff = Depends(require_perm(Perm.SUPPORT_CHAT)), session: AsyncSession = Depends(get_session)):
    if manage and Perm.TEMPLATES_MANAGE not in perms_of(me):
        raise HTTPException(403, "Управление шаблонами доступно администратору и руководителю поддержки")
    stmt = select(SupportTemplate).order_by(SupportTemplate.title, SupportTemplate.id)
    if not manage:
        stmt = stmt.where(SupportTemplate.is_active.is_(True))
    return [_template(t) for t in await session.scalars(stmt)]


@router.post("/support/templates", status_code=201)
async def create_template(body: TemplateBody, me: Staff = Depends(require_perm(Perm.TEMPLATES_MANAGE)), session: AsyncSession = Depends(get_session)):
    _validate_template(body)
    row = SupportTemplate(title=body.title.strip(), body=body.body.strip(), is_active=body.is_active, updated_by=me.telegram_id)
    session.add(row)
    await write_audit(session, me, "support.template.create", body.title)
    await session.commit()
    return _template(row)


def _validate_template(body: TemplateBody):
    if not body.title.strip() or not body.body.strip():
        raise HTTPException(422, "Заполните название и текст")
    unknown = set(re.findall(r"\{([^{}]+)\}", body.body)) - VARIABLES
    if unknown:
        raise HTTPException(422, "Неизвестные переменные: " + ", ".join(sorted(unknown)))


@router.put("/support/templates/{template_id}")
async def update_template(template_id: int, body: TemplateBody, me: Staff = Depends(require_perm(Perm.TEMPLATES_MANAGE)), session: AsyncSession = Depends(get_session)):
    _validate_template(body)
    row = await session.get(SupportTemplate, template_id)
    if row is None:
        raise HTTPException(404, "Шаблон не найден")
    row.updated_at = now()
    row.title, row.body, row.is_active, row.updated_by = body.title.strip(), body.body.strip(), body.is_active, me.telegram_id
    await write_audit(session, me, "support.template.update", str(row.id))
    await session.commit()
    return _template(row)
