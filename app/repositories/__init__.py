"""Thin async data-access helpers. Each function takes an AsyncSession."""
from __future__ import annotations

import html
import secrets

from aiogram.types import User as TgUser
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.base import AVITO_ID_BASE
from app.db.models import (
    MediaCache,
    MysteryBox,
    Order,
    OrderStatus,
    Product,
    RelayMap,
    SupportMessage,
    SupportOperator,
    User,
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
            is_admin=tg_user.id in get_settings().admin_ids,
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


async def get_user_by_thread(session: AsyncSession, thread_id: int) -> User | None:
    return await session.scalar(select(User).where(User.support_thread_id == thread_id))


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    return await session.scalar(
        select(User).where(func.lower(User.username) == username.lstrip("@").lower())
    )


async def get_user_by_avito_chat(session: AsyncSession, chat_id: str) -> User | None:
    return await session.scalar(select(User).where(User.avito_chat_id == chat_id))


async def get_or_create_avito_user(
    session: AsyncSession, avito_user_id: int, chat_id: str, name: str = ""
) -> User:
    """Find the Avito client by chat, creating a synthetic-id user if new."""
    user = await get_user_by_avito_chat(session, chat_id)
    if user is not None:
        return user
    user = User(
        id=AVITO_ID_BASE + avito_user_id,
        full_name=name or f"Avito {avito_user_id}",
        source="avito",
        avito_user_id=avito_user_id,
        avito_chat_id=chat_id,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        # Extremely unlikely (id collision) — surface it loudly.
        await session.rollback()
        raise
    return user


async def get_or_create_user_by_id(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        user = User(id=user_id, is_admin=user_id in get_settings().admin_ids)
        session.add(user)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            user = await session.get(User, user_id)
            if user is None:
                raise
    return user


async def set_banned(session: AsyncSession, user: User, banned: bool) -> None:
    user.is_banned = banned


async def set_state(session: AsyncSession, user: User, state: UserState) -> None:
    user.state = state.value


async def set_screen(session: AsyncSession, user: User, message_id: int | None) -> None:
    user.screen_message_id = message_id


def display_name(user: User) -> str:
    """Client name for embedding into HTML messages.

    full_name and username are client-controlled — escape them so a crafted
    name cannot inject markup into admin notifications or client screens.
    """
    handle = f" (@{user.username})" if user.username else ""
    return html.escape(f"{user.full_name}{handle}".strip())


def is_admin(user: User) -> bool:
    return user.is_admin or user.id in get_settings().admin_ids


# ---- Catalog ----
async def find_product_by_article(session: AsyncSession, article: str) -> Product | None:
    return await session.scalar(
        select(Product).where(Product.article == article, Product.available.is_(True))
    )


async def list_products(session: AsyncSession) -> list[Product]:
    result = await session.scalars(select(Product).order_by(Product.id.desc()))
    return list(result)


async def get_product(session: AsyncSession, product_id: int) -> Product | None:
    return await session.get(Product, product_id)


async def get_product_by_article(session: AsyncSession, article: str) -> Product | None:
    return await session.scalar(select(Product).where(Product.article == article))


async def create_product(
    session: AsyncSession,
    article: str,
    title: str,
    price: int,
    description: str | None = None,
    photo_file_id: str | None = None,
) -> Product:
    product = Product(
        article=article,
        title=title,
        price=price,
        description=description,
        photo_file_id=photo_file_id,
        available=True,
    )
    session.add(product)
    await session.flush()
    return product


async def toggle_product(session: AsyncSession, product: Product) -> None:
    product.available = not product.available


async def delete_product(session: AsyncSession, product: Product) -> None:
    await session.delete(product)


# ---- Mystery boxes ----
async def list_boxes(session: AsyncSession) -> list[MysteryBox]:
    result = await session.scalars(select(MysteryBox).order_by(MysteryBox.id.desc()))
    return list(result)


async def list_available_boxes(session: AsyncSession) -> list[MysteryBox]:
    result = await session.scalars(
        select(MysteryBox).where(MysteryBox.available.is_(True)).order_by(MysteryBox.price)
    )
    return list(result)


async def get_box(session: AsyncSession, box_id: int) -> MysteryBox | None:
    return await session.get(MysteryBox, box_id)


async def create_box(session: AsyncSession, title: str, price: int) -> MysteryBox:
    box = MysteryBox(title=title, price=price, available=True)
    session.add(box)
    await session.flush()
    return box


async def toggle_box(session: AsyncSession, box: MysteryBox) -> None:
    box.available = not box.available


async def delete_box(session: AsyncSession, box: MysteryBox) -> None:
    await session.delete(box)


async def count_rows(session: AsyncSession, model: type) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def count_banned_users(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count()).select_from(User).where(User.is_banned.is_(True))
    ) or 0


async def orders_by_status(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(
        select(Order.status, func.count()).group_by(Order.status)
    )
    return {status: count for status, count in rows.all()}


# ---- Orders ----
def _new_order_id() -> int:
    """Generate a random 9–10 digit public order ID."""
    return secrets.randbelow(9_900_000_000) + 100_000_000


async def create_order(session: AsyncSession, user: User, **fields: object) -> Order:
    # Retry on the extremely unlikely collision with an existing order ID.
    while True:
        order_id = _new_order_id()
        if await session.get(Order, order_id) is not None:
            continue
        order = Order(id=order_id, user_id=user.id, **fields)
        session.add(order)
        await session.flush()
        user.current_order_id = order.id
        return order


async def get_order(session: AsyncSession, order_id: int) -> Order | None:
    return await session.get(Order, order_id)


async def list_orders(session: AsyncSession, user_id: int) -> list[Order]:
    result = await session.scalars(
        select(Order).where(Order.user_id == user_id).order_by(Order.id.desc()).limit(10)
    )
    return list(result)


async def count_user_orders(session: AsyncSession, user_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(Order).where(Order.user_id == user_id)
    ) or 0


async def set_order_status(session: AsyncSession, order: Order, status: OrderStatus) -> None:
    order.status = status.value
    await session.flush()
    await session.refresh(order)


# ---- Media cache ----
async def get_cached_file_id(session: AsyncSession, key: str) -> str | None:
    cached = await session.get(MediaCache, key)
    if cached is None:
        return None
    # An empty file_id is never a valid media reference — treat it as absent so
    # callers fall back to uploading the banner from disk instead of sending
    # an empty photo (Telegram answers MEDIA_EMPTY).
    return cached.file_id or None


async def set_cached_file_id(session: AsyncSession, key: str, file_id: str) -> None:
    cached = await session.get(MediaCache, key)
    if cached is None:
        session.add(MediaCache(key=key, file_id=file_id))
    else:
        cached.file_id = file_id


async def delete_cached_file_id(session: AsyncSession, key: str) -> None:
    cached = await session.get(MediaCache, key)
    if cached is not None:
        await session.delete(cached)


# ---- Support relay ----
async def remember_relay(session: AsyncSession, support_message_id: int, user_id: int) -> None:
    session.add(RelayMap(support_message_id=support_message_id, user_id=user_id))


async def resolve_relay(session: AsyncSession, support_message_id: int) -> int | None:
    row = await session.get(RelayMap, support_message_id)
    return row.user_id if row else None


# ---- Web support panel ----
async def get_support_operator(
    session: AsyncSession, telegram_id: int
) -> SupportOperator | None:
    return await session.get(SupportOperator, telegram_id)


async def list_support_operators(session: AsyncSession) -> list[SupportOperator]:
    result = await session.scalars(
        select(SupportOperator).order_by(
            SupportOperator.is_active.desc(), SupportOperator.display_name
        )
    )
    return list(result)


async def save_support_operator(
    session: AsyncSession,
    telegram_id: int,
    display_name: str,
    is_active: bool = True,
) -> SupportOperator:
    operator = await get_support_operator(session, telegram_id)
    if operator is None:
        operator = SupportOperator(
            telegram_id=telegram_id, display_name=display_name, is_active=is_active
        )
        session.add(operator)
    else:
        operator.display_name = display_name
        operator.is_active = is_active
    await session.flush()
    return operator


async def record_support_message(
    session: AsyncSession,
    user_id: int,
    direction: str,
    body: str,
    channel: str,
    *,
    operator_telegram_id: int | None = None,
    operator_name: str | None = None,
    external_id: str | None = None,
) -> SupportMessage:
    if external_id is not None:
        existing = await session.scalar(
            select(SupportMessage).where(SupportMessage.external_id == external_id)
        )
        if existing is not None:
            return existing
    message = SupportMessage(
        user_id=user_id,
        direction=direction,
        body=body,
        channel=channel,
        operator_telegram_id=operator_telegram_id,
        operator_name=operator_name,
        external_id=external_id,
    )
    session.add(message)
    await session.flush()
    return message


async def list_support_clients(
    session: AsyncSession, query: str = "", offset: int = 0, limit: int = 50
) -> tuple[list[User], dict[int, SupportMessage], int]:
    filters = []
    query = query.strip()
    if query:
        if query.lstrip("-").isdigit():
            filters.append(User.id == int(query))
        else:
            pattern = f"%{query.lstrip('@')}%"
            filters.append(
                or_(
                    User.full_name.ilike(pattern),
                    User.username.ilike(pattern),
                    User.avito_chat_id.ilike(pattern),
                )
            )
    total = await session.scalar(select(func.count()).select_from(User).where(*filters)) or 0
    last_id = (
        select(func.max(SupportMessage.id))
        .where(SupportMessage.user_id == User.id)
        .scalar_subquery()
    )
    result = await session.scalars(
        select(User)
        .where(*filters)
        .order_by(func.coalesce(last_id, 0).desc(), User.created_at.desc())
        .offset(offset)
        .limit(min(limit, 100))
    )
    users = list(result)
    latest: dict[int, SupportMessage] = {}
    if users:
        user_ids = [user.id for user in users]
        latest_ids = (
            select(func.max(SupportMessage.id))
            .where(SupportMessage.user_id.in_(user_ids))
            .group_by(SupportMessage.user_id)
            .scalar_subquery()
        )
        rows = await session.scalars(
            select(SupportMessage).where(SupportMessage.id.in_(latest_ids))
        )
        latest = {message.user_id: message for message in rows}
    return users, latest, total


async def list_support_messages(
    session: AsyncSession, user_id: int, after: int = 0, limit: int = 200
) -> list[SupportMessage]:
    statement = select(SupportMessage).where(SupportMessage.user_id == user_id)
    if after:
        statement = statement.where(SupportMessage.id > after).order_by(SupportMessage.id)
    else:
        statement = statement.order_by(SupportMessage.id.desc())
    result = await session.scalars(statement.limit(min(limit, 500)))
    messages = list(result)
    if not after:
        messages.reverse()
    return messages
