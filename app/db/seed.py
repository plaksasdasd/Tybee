from __future__ import annotations

from sqlalchemy import delete, update

from app.config import get_settings
from app.db.base import session_factory
from app.db.models import Product, User

# Demo articles seeded by very old builds; purged from live databases on start.
DEMO_ARTICLES = ("482915", "100200", "777001", "555123", "321654")


async def seed() -> None:
    settings = get_settings()
    async with session_factory() as session:
        await session.execute(update(User).values(is_admin=False))
        for admin_id in settings.admin_ids:
            user = await session.get(User, admin_id)
            if user is None:
                session.add(User(id=admin_id, full_name="Administrator", is_admin=True))
            elif not user.is_admin:
                user.is_admin = True

        await session.commit()


async def purge_demo_products() -> None:
    """Remove leftover demo catalog rows from existing databases.

    Idempotent: a no-op once the catalog is clean. Real products added via
    /admin are never touched — only the exact demo article codes above.
    """
    async with session_factory() as session:
        await session.execute(delete(Product).where(Product.article.in_(DEMO_ARTICLES)))
        await session.commit()
