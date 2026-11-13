"""Catalog, Mystery Boxes and statistics (ТЗ-2, D.4). Admin only (``catalog.manage``)."""
from __future__ import annotations

import io
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Product

from app.catalog import service
from app.catalog.common import CatalogError
from app.catalog.import_xlsx import MAX_FILE_BYTES, import_products, preview_import
from app.services.product_photos import product_photo_path
from app.support.attachments import sniff_image
from panel.audit import write_audit
from panel.auth import csrf_guard, require_perm
from panel.deps import get_session
from panel.permissions import Perm

router = APIRouter(prefix="/api/catalog", tags=["catalog"], dependencies=[Depends(csrf_guard)])
manage = require_perm(Perm.CATALOG_MANAGE)


def _wrap(exc: CatalogError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


# ------------------------------------------------------------------ products
@router.get("/products")
async def list_products(
    q: str = "", page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100),
    _=Depends(manage), session: AsyncSession = Depends(get_session),
):
    return await service.list_products(session, q, page, per_page)


class ProductBody(BaseModel):
    article: str = Field(max_length=32)
    title: str = Field(max_length=300)
    price: int
    description: str | None = Field(default=None, max_length=4000)
    available: bool = True


class ProductPatch(BaseModel):
    article: str | None = Field(default=None, max_length=32)
    title: str | None = Field(default=None, max_length=300)
    price: int | None = None
    description: str | None = Field(default=None, max_length=4000)
    available: bool | None = None


@router.post("/products", status_code=201)
async def create_product(body: ProductBody, me=Depends(manage), session: AsyncSession = Depends(get_session)):
    try:
        product = await service.create_product(session, **body.model_dump())
    except CatalogError as exc:
        raise _wrap(exc) from None
    await write_audit(session, me, "catalog.product.create", product.article, {"id": product.id, "price": product.price})
    await session.commit()
    return service.product_json(product)


@router.patch("/products/{product_id}")
async def update_product(product_id: int, body: ProductPatch, me=Depends(manage), session: AsyncSession = Depends(get_session)):
    try:
        product = await service.get_product(session, product_id)
        changed = await service.update_product(session, product, body.model_dump(exclude_unset=True))
    except CatalogError as exc:
        raise _wrap(exc) from None
    if changed:
        action = "catalog.product.update"
        if changed == ["available"]:
            action = "catalog.product.enable" if product.available else "catalog.product.disable"
        await write_audit(session, me, action, product.article, {"id": product.id, "fields": changed})
        await session.commit()
    return service.product_json(product)


@router.delete("/products/{product_id}")
async def delete_product(product_id: int, me=Depends(manage), session: AsyncSession = Depends(get_session)):
    try:
        product = await service.get_product(session, product_id)
    except CatalogError as exc:
        raise _wrap(exc) from None
    article = product.article
    await service.delete_product(session, product)
    await write_audit(session, me, "catalog.product.delete", article, {"id": product_id})
    await session.commit()
    return {"ok": True}


@router.post("/products/{product_id}/photo")
async def upload_photo(product_id: int, file: UploadFile, me=Depends(manage), session: AsyncSession = Depends(get_session)):
    data = await file.read(service.PHOTO_MAX_BYTES + 1)
    try:
        product = await service.get_product(session, product_id)
        mime = await service.set_product_photo(session, product, data)
    except CatalogError as exc:
        raise _wrap(exc) from None
    await write_audit(session, me, "catalog.product.photo", product.article, {"id": product.id, "size": len(data), "mime": mime})
    await session.commit()
    return service.product_json(product)


@router.get("/products/{product_id}/photo")
async def get_photo(product_id: int, _=Depends(manage)):
    path = product_photo_path(product_id)
    if not path.is_file():
        raise HTTPException(404, "Фото не загружено с сайта")
    with open(path, "rb") as handle:
        mime = sniff_image(handle.read(16))
    return FileResponse(
        path, media_type=mime or "application/octet-stream",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-cache"},
    )


@router.get("/export")
async def export_xlsx(_=Depends(manage), session: AsyncSession = Depends(get_session)):
    import openpyxl
    from openpyxl.cell import WriteOnlyCell

    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("Каталог")
    ws.append(["Артикул", "Название", "Цена", "Описание", "Доступен"])
    for product in await session.scalars(select(Product).order_by(Product.article)):
        cells = []
        for value in (product.article, product.title, product.price, product.description or "", "да" if product.available else "нет"):
            cell = WriteOnlyCell(ws, value=value)
            if isinstance(value, str):
                # Preserve leading zeros and export user text literally, including '='.
                cell.data_type = "s"
            cells.append(cell)
        ws.append(cells)
    output = io.BytesIO()
    wb.save(output)
    wb.close()
    return Response(
        output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="catalog.xlsx"', "Cache-Control": "no-store"},
    )


@router.post("/import/preview")
async def preview_xlsx(file: UploadFile, _=Depends(manage), session: AsyncSession = Depends(get_session)):
    try:
        return await preview_import(session, await file.read(MAX_FILE_BYTES + 1))
    except CatalogError as exc:
        raise _wrap(exc) from None


@router.post("/import")
async def import_xlsx(file: UploadFile, choices: str = Form("{}"), me=Depends(manage), session: AsyncSession = Depends(get_session)):
    data = await file.read(MAX_FILE_BYTES + 1)
    try:
        try:
            selected = json.loads(choices)
            if not isinstance(selected, dict) or any(v not in ("current", "xlsx") for v in selected.values()):
                raise ValueError
        except (ValueError, TypeError):
            raise CatalogError("Некорректный выбор вариантов") from None
        result = await import_products(session, data, selected)
    except CatalogError as exc:
        raise _wrap(exc) from None
    await write_audit(session, me, "catalog.import", file.filename or "xlsx",
                      {"added": result.added, "updated": result.updated, "skipped": result.skipped, "errors": len(result.errors)})
    await session.commit()
    return result.as_dict()


# ------------------------------------------------------------- mystery boxes
class BoxBody(BaseModel):
    title: str = Field(max_length=300)
    price: int
    available: bool = True


class BoxPatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    price: int | None = None
    available: bool | None = None


@router.get("/boxes")
async def list_boxes(_=Depends(manage), session: AsyncSession = Depends(get_session)):
    return await service.list_boxes(session)


@router.post("/boxes", status_code=201)
async def create_box(body: BoxBody, me=Depends(manage), session: AsyncSession = Depends(get_session)):
    try:
        box = await service.create_box(session, **body.model_dump())
    except CatalogError as exc:
        raise _wrap(exc) from None
    await write_audit(session, me, "catalog.box.create", str(box.id), {"title": box.title, "price": box.price})
    await session.commit()
    return service.box_json(box)


@router.patch("/boxes/{box_id}")
async def update_box(box_id: int, body: BoxPatch, me=Depends(manage), session: AsyncSession = Depends(get_session)):
    try:
        box = await service.get_box(session, box_id)
        changed = await service.update_box(session, box, body.model_dump(exclude_unset=True))
    except CatalogError as exc:
        raise _wrap(exc) from None
    if changed:
        await write_audit(session, me, "catalog.box.update", str(box.id), {"fields": changed})
        await session.commit()
    return service.box_json(box)


@router.delete("/boxes/{box_id}")
async def delete_box(box_id: int, me=Depends(manage), session: AsyncSession = Depends(get_session)):
    try:
        box = await service.get_box(session, box_id)
    except CatalogError as exc:
        raise _wrap(exc) from None
    await service.delete_box(session, box)
    await write_audit(session, me, "catalog.box.delete", str(box_id))
    await session.commit()
    return {"ok": True}


# ---------------------------------------------------------------- statistics
@router.get("/stats")
async def stats(_=Depends(manage), session: AsyncSession = Depends(get_session)):
    return await service.stats(session)
