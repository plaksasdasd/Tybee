from __future__ import annotations

from sqlalchemy import select

from app.config import get_settings
from app.db.base import session_factory
from app.db.models import Product, User

# A small demo catalog ("library") of supported article codes from the source site.
SEED_PRODUCTS: list[dict[str, object]] = [
    {"article": "482915", "title": "Кроссовки Asics Gel-Kayano 30", "price": 14900},
    {"article": "100200", "title": "Куртка Uniqlo Ultra Light Down", "price": 6900},
    {"article": "777001", "title": "Фигурка Good Smile Nendoroid", "price": 8500},
    {"article": "555123", "title": "Часы Casio G-Shock GA-2100", "price": 11200},
    {"article": "321654", "title": "Чайный набор Kyusu (керамика)", "price": 5400},
]


async def seed() -> None:
    settings = get_settings()
    async with session_factory() as session:
        for item in SEED_PRODUCTS:
            exists = await session.scalar(select(Product).where(Product.article == item["article"]))
            if exists is None:
                session.add(Product(**item))

        for admin_id in settings.admin_ids:
            user = await session.get(User, admin_id)
            if user is None:
                session.add(User(id=admin_id, full_name="Administrator", is_admin=True))
            elif not user.is_admin:
                user.is_admin = True

        await session.commit()
