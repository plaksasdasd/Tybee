"""Thin async data-access helpers. Each function takes an AsyncSession."""
from __future__ import annotations

import html
import secrets

from aiogram.types import User as TgUser
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import (
    Cover,
    CoverMediaCache,
    CoverProductPhoto,
    MysteryBox,
    Order,
    OrderStatus,
    Product,
    User,
    UserCover,
)
from app.states import UserState


async def get_or_create_user(session: AsyncSession, tg_user: TgUser) -> User:
    user = await session.get(User, tg_user.id)
    full_name = tg_user.full_name or ""
    if user is None:
        user = User(
            id=tg_user.id,
            username=tg_user.username,
            full_name=full_name,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            user = await session.get(User, tg_user.id)
            if user is None:
                raise
            user.username = tg_user.username
            user.full_name = full_name
    else:
        user.username = tg_user.username
        user.full_name = full_name
    return user


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


# ---- Covers / per-cover client state ----
async def get_cover(session: AsyncSession, cover_id: int) -> Cover | None:
    return await session.get(Cover, cover_id)


async def get_or_create_user_cover(
    session: AsyncSession, user: User, cover_id: int
) -> UserCover:
    uc = await session.get(UserCover, (user.id, cover_id))
    if uc is None:
        uc = UserCover(user_id=user.id, cover_id=cover_id, state=UserState.MENU.value)
        session.add(uc)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            uc = await session.get(UserCover, (user.id, cover_id))
            if uc is None:
                raise
    return uc


async def set_state(session: AsyncSession, uc: UserCover, state: UserState) -> None:
    uc.state = state.value


async def set_screen(session: AsyncSession, uc: UserCover, message_id: int | None) -> None:
    uc.screen_message_id = message_id


def display_name(user: User) -> str:
    """Client name for embedding into HTML messages.

    full_name and username are client-controlled — escape them so a crafted
    name cannot inject markup into admin notifications or client screens.
    """
    handle = f" (@{user.username})" if user.username else ""
    return html.escape(f"{user.full_name}{handle}".strip())


# ---- Catalog ----
async def find_product_by_article(session: AsyncSession, article: str) -> Product | None:
    return await session.scalar(
        select(Product).where(Product.article == article, Product.available.is_(True))
    )


# ---- Mystery boxes ----
async def list_available_boxes(session: AsyncSession) -> list[MysteryBox]:
    result = await session.scalars(
        select(MysteryBox).where(MysteryBox.available.is_(True)).order_by(MysteryBox.price)
    )
    return list(result)


async def get_box(session: AsyncSession, box_id: int) -> MysteryBox | None:
    return await session.get(MysteryBox, box_id)


# ---- Orders ----
def _new_order_id() -> int:
    """Generate a random 9–10 digit public order ID."""
    return secrets.randbelow(1_900_000_000) + 100_000_000


async def create_order(
    session: AsyncSession, user: User, uc: UserCover, **fields: object
) -> Order:
    # Retry on the extremely unlikely collision with an existing order ID.
    while True:
        order_id = _new_order_id()
        if await session.get(Order, order_id) is not None:
            continue
        order = Order(id=order_id, user_id=user.id, cover_id=uc.cover_id, **fields)
        session.add(order)
        await session.flush()
        uc.current_order_id = order.id
        return order


async def get_order(session: AsyncSession, order_id: int) -> Order | None:
    return await session.get(Order, order_id)


async def list_orders(session: AsyncSession, user_id: int, cover_id: int) -> list[Order]:
    result = await session.scalars(
        select(Order)
        .where(Order.user_id == user_id, Order.cover_id == cover_id)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(get_settings().profile_orders_limit)
    )
    return list(result)


async def count_user_orders(session: AsyncSession, user_id: int, cover_id: int) -> int:
    return await session.scalar(
        select(func.count())
        .select_from(Order)
        .where(Order.user_id == user_id, Order.cover_id == cover_id)
    ) or 0


async def set_order_status(session: AsyncSession, order: Order, status: OrderStatus) -> None:
    order.status = status.value
    await session.flush()
    await session.refresh(order)


# ---- Media cache (per cover: file_ids are bot-specific) ----
async def get_cached_banner(
    session: AsyncSession, cover_id: int, key: str
) -> CoverMediaCache | None:
    cached = await session.get(CoverMediaCache, (cover_id, key))
    # An empty file_id is never a valid media reference — treat it as absent so
    # callers fall back to uploading the banner from disk (Telegram: MEDIA_EMPTY).
    if cached is None or not cached.file_id:
        return None
    return cached


async def set_cached_banner(
    session: AsyncSession, cover_id: int, key: str, file_id: str, fingerprint: str
) -> None:
    cached = await session.get(CoverMediaCache, (cover_id, key))
    if cached is None:
        session.add(
            CoverMediaCache(cover_id=cover_id, key=key, file_id=file_id, fingerprint=fingerprint)
        )
    else:
        cached.file_id = file_id
        cached.fingerprint = fingerprint


async def delete_cached_banner(session: AsyncSession, cover_id: int, key: str) -> None:
    cached = await session.get(CoverMediaCache, (cover_id, key))
    if cached is not None:
        await session.delete(cached)


# ---- Product photos per cover ----
async def get_product_photo_row(
    session: AsyncSession, product_id: int, cover_id: int
) -> CoverProductPhoto | None:
    return await session.get(CoverProductPhoto, (product_id, cover_id))


async def set_product_photo(
    session: AsyncSession, product_id: int, cover_id: int, file_id: str
) -> None:
    row = await session.get(CoverProductPhoto, (product_id, cover_id))
    if row is None:
        session.add(CoverProductPhoto(product_id=product_id, cover_id=cover_id, file_id=file_id))
    else:
        row.file_id = file_id


async def delete_product_photo(session: AsyncSession, product_id: int, cover_id: int) -> None:
    row = await session.get(CoverProductPhoto, (product_id, cover_id))
    if row is not None:
        await session.delete(row)
