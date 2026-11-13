"""Broadcast validation and recipients (shared by the site and the bot-process worker)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.common import CatalogError
from app.covers import loader
from app.db.models import Cover, User, UserCover

TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024
IMAGE_MAX_BYTES = 10 * 1024 * 1024


def check_body(body: str, with_image: bool) -> str:
    body = (body or "").strip()
    if not body:
        raise CatalogError("Пустой текст рассылки")
    errors = loader._check_html("Текст", body)
    if errors:
        raise CatalogError("; ".join(errors))
    limit = CAPTION_LIMIT if with_image else TEXT_LIMIT
    if loader._visible_length(body) > limit:
        raise CatalogError(f"Текст длиннее {limit} символов" + (" (подпись к картинке)" if with_image else ""))
    return body


async def resolve_cover_ids(session: AsyncSession, cover_ids: list[int] | None) -> list[int]:
    """Selected covers, or every enabled cover when the selection is empty."""
    stmt = select(Cover.id).where(Cover.enabled.is_(True))
    if cover_ids:
        stmt = select(Cover.id).where(Cover.id.in_(cover_ids))
    return list(await session.scalars(stmt.order_by(Cover.id)))


@dataclass(frozen=True)
class Recipient:
    user_id: int
    cover_id: int


async def recipients(
    session: AsyncSession, cover_ids: list[int], *, after_user_id: int | None = None, limit: int | None = None
) -> list[Recipient]:
    """One recipient per person: Telegram clients of the covers, not banned; the cover
    is the one among ``cover_ids`` where the client was seen last. Ordered by user id."""
    if not cover_ids:
        return []
    users = (
        select(User.id)
        .join(UserCover, UserCover.user_id == User.id)
        .where(UserCover.cover_id.in_(cover_ids), User.source == "telegram", User.is_banned.is_(False))
        .distinct()
        .order_by(User.id)
    )
    if after_user_id is not None:
        users = users.where(User.id > after_user_id)
    if limit is not None:
        users = users.limit(limit)
    ids = list(await session.scalars(users))
    if not ids:
        return []
    rows = await session.execute(
        select(UserCover.user_id, UserCover.cover_id)
        .where(UserCover.user_id.in_(ids), UserCover.cover_id.in_(cover_ids))
        .order_by(UserCover.user_id, UserCover.last_seen_at.desc(), UserCover.cover_id)
    )
    best: dict[int, int] = {}
    for user_id, cover_id in rows.all():
        best.setdefault(user_id, cover_id)
    return [Recipient(uid, best[uid]) for uid in ids]


async def count_recipients(session: AsyncSession, cover_ids: list[int]) -> int:
    return len(await recipients(session, cover_ids))
