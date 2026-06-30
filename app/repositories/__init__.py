"""Thin async data-access helpers. Each function takes an AsyncSession."""
from __future__ import annotations

from aiogram.types import User as TgUser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import MediaCache, Order, OrderStatus, Product, RelayMap, User
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
        await session.flush()
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


async def get_or_create_user_by_id(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        user = User(id=user_id, is_admin=user_id in get_settings().admin_ids)
        session.add(user)
        await session.flush()
    return user


async def set_banned(session: AsyncSession, user: User, banned: bool) -> None:
    user.is_banned = banned


async def set_state(session: AsyncSession, user: User, state: UserState) -> None:
    user.state = state.value


async def set_screen(session: AsyncSession, user: User, message_id: int | None) -> None:
    user.screen_message_id = message_id


def display_name(user: User) -> str:
    handle = f" (@{user.username})" if user.username else ""
    return f"{user.full_name}{handle}".strip()


# ---- Catalog ----
async def find_product_by_article(session: AsyncSession, article: str) -> Product | None:
    return await session.scalar(
        select(Product).where(Product.article == article, Product.available.is_(True))
    )


async def list_products(session: AsyncSession) -> list[Product]:
    result = await session.scalars(select(Product).order_by(Product.id.desc()))
    return list(result)


async def list_available_products(session: AsyncSession) -> list[Product]:
    result = await session.scalars(
        select(Product).where(Product.available.is_(True)).order_by(Product.title)
    )
    return list(result)


async def get_product(session: AsyncSession, product_id: int) -> Product | None:
    return await session.get(Product, product_id)


async def get_product_by_article(session: AsyncSession, article: str) -> Product | None:
    return await session.scalar(select(Product).where(Product.article == article))


async def create_product(
    session: AsyncSession, article: str, title: str, price: int
) -> Product:
    product = Product(article=article, title=title, price=price, available=True)
    session.add(product)
    await session.flush()
    return product


async def toggle_product(session: AsyncSession, product: Product) -> None:
    product.available = not product.available


async def delete_product(session: AsyncSession, product: Product) -> None:
    await session.delete(product)


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
async def create_order(session: AsyncSession, user: User, **fields: object) -> Order:
    order = Order(user_id=user.id, **fields)
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


async def set_order_status(session: AsyncSession, order: Order, status: OrderStatus) -> None:
    order.status = status.value


# ---- Media cache ----
async def get_cached_file_id(session: AsyncSession, key: str) -> str | None:
    cached = await session.get(MediaCache, key)
    return cached.file_id if cached else None


async def set_cached_file_id(session: AsyncSession, key: str, file_id: str) -> None:
    cached = await session.get(MediaCache, key)
    if cached is None:
        session.add(MediaCache(key=key, file_id=file_id))
    else:
        cached.file_id = file_id


# ---- Support relay ----
async def remember_relay(session: AsyncSession, support_message_id: int, user_id: int) -> None:
    session.add(RelayMap(support_message_id=support_message_id, user_id=user_id))


async def resolve_relay(session: AsyncSession, support_message_id: int) -> int | None:
    row = await session.get(RelayMap, support_message_id)
    return row.user_id if row else None
