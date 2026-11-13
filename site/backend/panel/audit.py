from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, Staff


async def write_audit(
    session: AsyncSession, staff: Staff, action: str, target: str | None, details: dict[str, Any] | None = None
) -> None:
    """Stage an audit row. The caller commits, so it lands in the same transaction as the change.

    ``details`` must never contain tokens or secrets."""
    session.add(AuditLog(actor_id=staff.telegram_id, action=action, target=target, details=details))


# ------------------------------------------------------------ human-readable targets
_STAFF_TARGET = (
    "auth.", "staff.create", "staff.update", "staff.delete", "staff.activate", "staff.deactivate",
    "staff.role.", "staff.invite.accept", "support.release", "support.shift", "broadcast.test",
    "finance.requisites.",
)
_THREAD_TARGET = ("support.reply", "support.claim", "support.transfer", "support.close", "support.invoice",
                  "support.ban", "support.unban")
_REPORTS = {"operations": "операции", "staff": "по сотрудникам", "revenue": "выручка"}


def _kind(action: str) -> str | None:
    if action.startswith(_STAFF_TARGET):
        return "staff"
    if action.startswith(_THREAD_TARGET):
        return "thread"
    for prefix, kind in (
        ("support.order.", "order"), ("broadcast.", "broadcast"), ("catalog.product.", "product"),
        ("catalog.box.", "box"), ("catalog.import", "file"), ("cover.", "cover"), ("avito.profile.", "avito"),
        ("finance.op.", "op"), ("finance.operation.", "op"), ("finance.payout.", "payout"), ("finance.export", "report"),
        ("finance.payroll.", "staff"), ("finance.review.", "review"),
    ):
        if action.startswith(prefix):
            return kind
    return None


def _int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _client(full_name: str | None, username: str | None) -> str:
    return (full_name or "").strip() or (f"@{username}" if username else "")


async def target_labels(session: AsyncSession, rows: list[AuditLog]) -> dict[int, str]:
    """audit row id -> readable "object" text, resolved with one query per object kind."""
    from app.db.models import AvitoProfile, Cover, MysteryBox, Order, Product, SupportThread, User

    want: dict[str, set] = {}
    for r in rows:
        kind = _kind(r.action) if r.target else None
        if kind in ("staff", "thread", "order", "box"):
            if (n := _int(r.target)) is not None:
                want.setdefault(kind, set()).add(n)
        elif kind in ("product", "cover", "avito"):
            want.setdefault(kind, set()).add(r.target)

    staff, threads, orders, boxes, products, covers, avito = {}, {}, {}, {}, {}, {}, {}
    if ids := want.get("staff"):
        staff = dict((await session.execute(select(Staff.telegram_id, Staff.display_name).where(Staff.telegram_id.in_(ids)))).all())
    if ids := want.get("thread"):
        res = await session.execute(
            select(SupportThread.id, User.full_name, User.username).join(User, User.id == SupportThread.user_id)
            .where(SupportThread.id.in_(ids))
        )
        threads = {tid: _client(fn, un) for tid, fn, un in res}
    if ids := want.get("order"):
        res = await session.execute(
            select(Order.id, Order.title, User.full_name, User.username).join(User, User.id == Order.user_id, isouter=True)
            .where(Order.id.in_(ids))
        )
        orders = {oid: " · ".join(x for x in (title, _client(fn, un)) if x) for oid, title, fn, un in res}
    if ids := want.get("box"):
        boxes = dict((await session.execute(select(MysteryBox.id, MysteryBox.title).where(MysteryBox.id.in_(ids)))).all())
    if ids := want.get("product"):
        products = dict((await session.execute(select(Product.article, Product.title).where(Product.article.in_(ids)))).all())
    if ids := want.get("cover"):
        covers = dict((await session.execute(select(Cover.slug, Cover.bot_username).where(Cover.slug.in_(ids)))).all())
    if ids := want.get("avito"):
        avito = dict((await session.execute(select(AvitoProfile.slug, AvitoProfile.title).where(AvitoProfile.slug.in_(ids)))).all())

    out: dict[int, str] = {}
    for r in rows:
        t, d = r.target, r.details or {}
        if not t:
            continue
        kind, n = _kind(r.action), _int(t)
        if kind == "staff":
            name = staff.get(n) or d.get("name")
            label = f"Сотрудник {name}" if name else f"Сотрудник (ID {t})"
        elif kind == "thread":
            label = f"Диалог #{t}" + (f" · {threads[n]}" if threads.get(n) else "")
        elif kind == "order":
            label = f"Заказ #{t}" + (f" · {orders[n]}" if orders.get(n) else "")
        elif kind == "broadcast":
            label = f"Рассылка #{t}"
        elif kind == "product":
            label = f"Товар {t}" + (f" · {products[t]}" if products.get(t) else "")
        elif kind == "box":
            title = boxes.get(n) or d.get("title")
            label = f"Mystery Box «{title}»" if title else f"Mystery Box #{t}"
        elif kind == "file":
            label = f"Файл {t}"
        elif kind == "cover":
            label = f"Бот @{covers[t]} ({t})" if covers.get(t) else f"Бот {t}"
        elif kind == "avito":
            label = f"Авито «{avito[t]}»" if avito.get(t) else f"Авито {t}"
        elif kind == "op":
            label = f"Операция #{t}"
        elif kind == "payout":
            label = f"Выплата #{t}"
        elif kind == "report":
            label = f"Отчёт «{_REPORTS.get(t, t)}»"
        elif kind == "review":
            label = f"Отзыв #{t}"
        else:
            continue
        out[r.id] = label
    return out
