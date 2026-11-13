"""Products, Mystery Boxes, product photos and statistics (formerly the bots' /admin panel)."""
from __future__ import annotations

import os
import tempfile
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.common import ARTICLE_RE, DESCRIPTION_MAX, CatalogError, check_price, check_title
from app.db.models import Cover, CoverProductPhoto, MysteryBox, Order, Product, User, UserCover
from app.services.product_photos import product_photo_path
from app.support.attachments import validate_image
from app.support.common import ORDER_STATUS_LABELS

PHOTO_MAX_BYTES = 10 * 1024 * 1024


# ------------------------------------------------------------------ products
def product_json(p: Product) -> dict[str, Any]:
    return {
        "id": p.id, "article": p.article, "title": p.title, "price": p.price, "description": p.description,
        "available": p.available, "has_photo": product_photo_path(p.id).is_file() or bool(p.photo_file_id),
    }


async def list_products(session: AsyncSession, q: str = "", page: int = 1, per_page: int = 20) -> dict[str, Any]:
    stmt = select(Product)
    count = select(func.count()).select_from(Product)
    q = (q or "").strip()
    if q:
        like = f"%{q.lower()}%"
        cond = or_(Product.article.like(f"%{q}%"), func.lower(Product.title).like(like), Product.title.like(f"%{q}%"))
        stmt, count = stmt.where(cond), count.where(cond)
    total = int(await session.scalar(count) or 0)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    rows = await session.scalars(stmt.order_by(Product.id.desc()).offset((page - 1) * per_page).limit(per_page))
    return {"items": [product_json(p) for p in rows], "total": total, "page": page, "pages": pages}


async def get_product(session: AsyncSession, product_id: int) -> Product:
    product = await session.get(Product, product_id)
    if product is None:
        raise CatalogError("Товар не найден", 404)
    return product


def _check_article(article: str) -> str:
    article = (article or "").strip()
    if not ARTICLE_RE.match(article):
        raise CatalogError("Артикул — ровно 6 цифр")
    return article


def _check_description(description: str | None) -> str | None:
    description = (description or "").strip() or None
    if description and len(description) > DESCRIPTION_MAX:
        raise CatalogError(f"Описание длиннее {DESCRIPTION_MAX} символов")
    return description


async def _article_taken(session: AsyncSession, article: str, exclude_id: int | None = None) -> bool:
    stmt = select(Product.id).where(Product.article == article)
    if exclude_id is not None:
        stmt = stmt.where(Product.id != exclude_id)
    return await session.scalar(stmt) is not None


async def create_product(
    session: AsyncSession, *, article: str, title: str, price: int, description: str | None = None,
    available: bool = True,
) -> Product:
    article = _check_article(article)
    if await _article_taken(session, article):
        raise CatalogError(f"Артикул {article} уже существует", 409)
    product = Product(
        article=article, title=check_title(title), price=check_price(price),
        description=_check_description(description), photo_file_id=None, available=available,
    )
    session.add(product)
    await session.flush()
    return product


async def update_product(session: AsyncSession, product: Product, fields: dict[str, Any]) -> list[str]:
    """Apply validated changes; returns the names of the changed fields."""
    changed: list[str] = []
    if "article" in fields and fields["article"] is not None:
        article = _check_article(fields["article"])
        if article != product.article:
            if await _article_taken(session, article, product.id):
                raise CatalogError(f"Артикул {article} уже существует", 409)
            product.article = article
            changed.append("article")
    if "title" in fields and fields["title"] is not None:
        title = check_title(fields["title"])
        if title != product.title:
            product.title = title
            changed.append("title")
    if "price" in fields and fields["price"] is not None:
        price = check_price(fields["price"])
        if price != product.price:
            product.price = price
            changed.append("price")
    if "description" in fields:
        description = _check_description(fields["description"])
        if description != product.description:
            product.description = description
            changed.append("description")
    if "available" in fields and fields["available"] is not None and bool(fields["available"]) != product.available:
        product.available = bool(fields["available"])
        changed.append("available")
    await session.flush()
    return changed


async def delete_product(session: AsyncSession, product: Product) -> None:
    """Same semantics as the old /admin button: the row is removed (orders keep their own copy)."""
    await session.execute(delete(CoverProductPhoto).where(CoverProductPhoto.product_id == product.id))
    await session.delete(product)
    await session.flush()


def _atomic_write(path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


async def set_product_photo(session: AsyncSession, product: Product, data: bytes) -> str:
    """Store the photo where the bots look for it and drop every cached Telegram file_id:
    each cover re-uploads it on the next show (ТЗ-2, D.2). Returns the MIME type."""
    if not data:
        raise CatalogError("Пустой файл")
    if len(data) > PHOTO_MAX_BYTES:
        raise CatalogError("Фото больше 10 МБ")
    mime = validate_image(data)
    if mime is None:
        raise CatalogError("Допустимы только JPEG, PNG и WebP")
    _atomic_write(product_photo_path(product.id), data)
    await session.execute(delete(CoverProductPhoto).where(CoverProductPhoto.product_id == product.id))
    product.photo_file_id = None
    await session.flush()
    return mime


# ------------------------------------------------------------- mystery boxes
def box_json(b: MysteryBox) -> dict[str, Any]:
    return {"id": b.id, "title": b.title, "price": b.price, "available": b.available}


async def list_boxes(session: AsyncSession) -> list[dict[str, Any]]:
    return [box_json(b) for b in await session.scalars(select(MysteryBox).order_by(MysteryBox.id.desc()))]


async def get_box(session: AsyncSession, box_id: int) -> MysteryBox:
    box = await session.get(MysteryBox, box_id)
    if box is None:
        raise CatalogError("Бокс не найден", 404)
    return box


async def create_box(session: AsyncSession, *, title: str, price: int, available: bool = True) -> MysteryBox:
    box = MysteryBox(title=check_title(title), price=check_price(price), available=available)
    session.add(box)
    await session.flush()
    return box


async def update_box(session: AsyncSession, box: MysteryBox, fields: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    if fields.get("title") is not None:
        title = check_title(fields["title"])
        if title != box.title:
            box.title, changed = title, [*changed, "title"]
    if fields.get("price") is not None:
        price = check_price(fields["price"])
        if price != box.price:
            box.price, changed = price, [*changed, "price"]
    if fields.get("available") is not None and bool(fields["available"]) != box.available:
        box.available, changed = bool(fields["available"]), [*changed, "available"]
    await session.flush()
    return changed


async def delete_box(session: AsyncSession, box: MysteryBox) -> None:
    await session.delete(box)
    await session.flush()


# ---------------------------------------------------------------- statistics
async def stats(session: AsyncSession) -> dict[str, Any]:
    """The old ``adm:stats`` numbers plus a per-cover breakdown."""
    async def count(stmt) -> int:
        return int(await session.scalar(stmt) or 0)

    by_status = dict((await session.execute(select(Order.status, func.count()).group_by(Order.status))).all())
    covers = list(await session.scalars(select(Cover).order_by(Cover.id)))
    clients = dict((await session.execute(select(UserCover.cover_id, func.count()).group_by(UserCover.cover_id))).all())
    orders = dict((await session.execute(
        select(Order.cover_id, func.count()).where(Order.cover_id.is_not(None)).group_by(Order.cover_id)
    )).all())
    paid = dict((await session.execute(
        select(Order.cover_id, func.count()).where(Order.cover_id.is_not(None), Order.paid_at.is_not(None)).group_by(Order.cover_id)
    )).all())
    return {
        "users": await count(select(func.count()).select_from(User)),
        "banned": await count(select(func.count()).select_from(User).where(User.is_banned.is_(True))),
        "products": await count(select(func.count()).select_from(Product)),
        "boxes": await count(select(func.count()).select_from(MysteryBox)),
        "orders": await count(select(func.count()).select_from(Order)),
        "by_status": [
            {"status": code, "label": ORDER_STATUS_LABELS.get(code, code), "count": int(n)}
            for code, n in sorted(by_status.items())
        ],
        "covers": [
            {"slug": c.slug, "enabled": c.enabled, "clients": int(clients.get(c.id, 0)),
             "orders": int(orders.get(c.id, 0)), "paid_orders": int(paid.get(c.id, 0))}
            for c in covers
        ],
    }
