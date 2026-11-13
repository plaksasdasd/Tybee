"""Product photos across covers.

A Telegram ``file_id`` is valid only for the bot that produced it, so the
photo itself is kept on disk (``<data_dir>/products/<product_id>``) and every
cover gets its own file_id in ``cover_product_photos``.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.config import get_settings
from app.covers import registry
from app.db.models import Cover, Product

logger = logging.getLogger(__name__)


def product_photo_path(product_id: int) -> Path:
    return Path(get_settings().data_dir) / "products" / str(product_id)


async def save_product_photo_file(bot: Bot, file_id: str, product_id: int) -> Path | None:
    """Download a product photo with ``bot`` to disk (atomic). None on failure."""
    path = product_photo_path(product_id)
    tmp = path.with_name(path.name + ".part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        await bot.download(file_id, destination=tmp)
        os.replace(tmp, path)
        return path
    except Exception:
        logger.exception("Could not save photo of product %s to disk", product_id)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None


async def get_product_photo(
    session: AsyncSession, product: Product, cover: Cover
) -> str | FSInputFile | None:
    """Photo to show for ``product`` in ``cover``: a file_id valid for that
    cover's bot, or a local file to upload (its file_id is then cached by
    ``show_screen(..., product_id=...)``). None when the product has no photo."""
    row = await repo.get_product_photo_row(session, product.id, cover.id)
    if row is not None and row.file_id:
        return row.file_id
    path = product_photo_path(product.id)
    if path.is_file():
        return FSInputFile(path)
    if not product.photo_file_id:
        return None
    settings = get_settings()
    if cover.slug == settings.support_cover_slug:
        # The legacy file_id was produced by this cover's bot.
        return product.photo_file_id
    source = registry.get_by_slug(settings.support_cover_slug)
    if source is None:
        return None
    saved = await save_product_photo_file(source.bot, product.photo_file_id, product.id)
    return FSInputFile(saved) if saved is not None else None
