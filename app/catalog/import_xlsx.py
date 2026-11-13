"""XLSX catalog import — the same format and rules as the former ``/admin`` import
(``process_import_document``), without aiogram, with an error list per row."""
from __future__ import annotations

import io
import asyncio
import zipfile
from decimal import Decimal, InvalidOperation
from itertools import islice
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.common import ARTICLE_RE, DESCRIPTION_MAX, PRICE_MAX, TITLE_MAX, CatalogError
from app.db.models import Product

MAX_ROWS = 500
MAX_FILE_BYTES = 10 * 1024 * 1024
_TRUE = ("1", "true", "да", "yes", "y", "+", "available", "в наличии", "on", "вкл")
_FALSE = ("0", "false", "нет", "no", "n", "-", "off", "недоступен", "выкл")


@dataclass
class ParsedRow:
    row: int
    article: str
    title: str
    price: int
    description: str | None
    available: bool


@dataclass
class RowError:
    row: int
    reason: str


@dataclass
class ParseResult:
    rows: list[ParsedRow] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    truncated: bool = False


@dataclass
class ImportResult:
    added: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[RowError] = field(default_factory=list)
    truncated: bool = False

    def as_dict(self) -> dict:
        return {
            "added": self.added, "updated": self.updated, "skipped": self.skipped, "truncated": self.truncated,
            "errors": [{"row": e.row, "reason": e.reason} for e in self.errors],
        }


def _parse_available(value: object | None) -> bool:
    if value is None or value == "":
        return True
    s = str(value).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    raise ValueError("доступен — укажите да/нет, 1/0 или true/false")


def norm_article(value: object | None) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if s.endswith(".0"):  # Excel numeric articles like 482915.0
        s = s[:-2]
    return s.replace(" ", "").replace("'", "")


def _columns(rows: list[tuple]) -> tuple[int, int, int, int | None, int | None, int]:
    """(article, title, price, description, available, first data row index), header-aware."""
    article_idx, title_idx, price_idx, desc_idx, avail_idx = 0, 1, 2, 3, 4
    start_idx = 0
    header = rows[0] if rows and rows[0] else None
    if header:
        header_lower = [str(c or "").strip().lower() for c in header]
        combined = " ".join(header_lower)
        has_keywords = any(
            k in combined
            for k in ("артикул", "sku", "article", "название", "name", "title", "цена", "price", "опис", "text", "desc")
        )
        first_article = norm_article(header[0]) if len(header) > 0 else ""
        if not ARTICLE_RE.fullmatch(first_article) and has_keywords:
            looks_like_header = has_keywords
            if not looks_like_header and len(rows) > 1 and rows[1]:
                second = norm_article(rows[1][0]) if len(rows[1]) > 0 else ""
                looks_like_header = bool(ARTICLE_RE.match(second))
            if looks_like_header:
                def find(keywords: list[str]) -> int | None:
                    for i, h in enumerate(header_lower):
                        if any(k in h for k in keywords):
                            return i
                    return None

                a_idx = find(["артикул", "sku", "article", "код"])
                t_idx = find(["название", "name", "title"])
                p_idx = find(["цена", "price", "стоимость"])
                d_idx = find(["опис", "text", "desc"])
                av_idx = find(["доступ", "available", "актив", "в наличии"])
                article_idx = a_idx if a_idx is not None else 0
                title_idx = t_idx if t_idx is not None else 1
                price_idx = p_idx if p_idx is not None else 2
                if d_idx is not None:
                    desc_idx = d_idx
                else:
                    used = {article_idx, title_idx, price_idx}
                    if av_idx is not None:
                        used.add(av_idx)
                    desc_idx = next((i for i in range(len(header)) if i not in used), None)
                avail_idx = av_idx
                start_idx = 1
    return article_idx, title_idx, price_idx, desc_idx, avail_idx, start_idx


def parse_rows(rows: list[tuple]) -> ParseResult:
    result = ParseResult()
    if not rows:
        raise CatalogError("Файл пустой")
    article_idx, title_idx, price_idx, desc_idx, avail_idx, start_idx = _columns(rows)
    nonempty = [(i, row) for i, row in enumerate(rows[start_idx:], start=start_idx + 1)
                if row and any(c is not None and str(c).strip() for c in row)]
    if len(nonempty) > MAX_ROWS:
        raise CatalogError("В файле больше 500 строк. Разделите его на несколько файлов")
    seen: set[str] = set()
    data_rows = nonempty
    for number, row in data_rows:
        if row is None or all(c is None or str(c).strip() == "" for c in row):
            continue

        def cell(i: int | None) -> object | None:
            return None if i is None or i >= len(row) else row[i]

        price_raw = cell(price_idx)
        article = norm_article(cell(article_idx))
        title = str(cell(title_idx) or "").strip()
        price_str = str(price_raw).strip() if price_raw is not None else ""
        if price_str.endswith(".0"):
            price_str = price_str[:-2]
        description = str(cell(desc_idx) or "").strip() or None
        try:
            available = _parse_available(cell(avail_idx) if avail_idx is not None else None)
        except ValueError as exc:
            result.errors.append(RowError(number, str(exc)))
            continue

        if not article or not ARTICLE_RE.match(article):
            result.errors.append(RowError(number, f"артикул '{article}' — нужны 6 цифр"))
            continue
        if article in seen:
            result.errors.append(RowError(number, f"артикул {article} повторяется в файле"))
            continue
        seen.add(article)
        if not title:
            result.errors.append(RowError(number, "пустое название"))
            continue
        try:
            clean = price_str.replace(" ", "").replace(",", ".").replace("₽", "").strip()
            if len(clean) > 100:
                raise ValueError("слишком длинная цена")
            value = Decimal(clean)
            if not value.is_finite() or value <= 0 or value > PRICE_MAX or value != value.to_integral_value():
                raise ValueError("цена должна быть целым числом рублей")
            price = int(value)
        except Exception:  # noqa: BLE001
            result.errors.append(RowError(number, f"цена '{price_raw}' — нужно число от 1 до {PRICE_MAX}"))
            continue
        result.rows.append(ParsedRow(
            row=number, article=article, title=title[:TITLE_MAX],
            price=price, description=description[:DESCRIPTION_MAX] if description else None, available=available,
        ))
    return result


def parse_xlsx(data: bytes) -> ParseResult:
    if not data:
        raise CatalogError("Пустой файл")
    if len(data) > MAX_FILE_BYTES:
        raise CatalogError("Файл больше 10 МБ")
    if not data.startswith(b"PK"):
        raise CatalogError("Это не .xlsx (нет сигнатуры ZIP)")
    import openpyxl

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(i.file_size for i in entries) > 40 * 1024 * 1024:
                raise CatalogError("Содержимое XLSX слишком большое")
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True, keep_links=False)
        try:
            ws = wb.active
            if ws is None:
                raise CatalogError("В книге нет листов")
            if getattr(ws, "max_column", 0) and ws.max_column > 64:
                raise CatalogError("В файле больше 64 столбцов")
            rows = list(islice(ws.iter_rows(values_only=True, max_col=64), MAX_ROWS + 2))
            if len(rows) > MAX_ROWS + 1:
                raise CatalogError("В файле больше 500 строк. Разделите его на несколько файлов")
        finally:
            wb.close()
    except CatalogError:
        raise
    except Exception as exc:  # noqa: BLE001 - corrupt/unsupported workbook
        raise CatalogError("Не удалось прочитать файл: нужен .xlsx в формате «артикул, название, цена, [описание], [доступен]»") from exc
    return parse_rows(rows)


def row_json(row: ParsedRow) -> dict:
    return {key: getattr(row, key) for key in ("article", "title", "price", "description", "available")}


def differs(product: Product, row: ParsedRow) -> bool:
    return any(getattr(product, key) != getattr(row, key) for key in ("title", "price", "available")) or (product.description or "") != (row.description or "")


async def existing_products(session: AsyncSession, parsed: ParseResult) -> dict[str, Product]:
    return {p.article: p for p in await session.scalars(
        select(Product).where(Product.article.in_([r.article for r in parsed.rows]))
    )}


async def preview_import(session: AsyncSession, data: bytes) -> dict:
    parsed = await asyncio.to_thread(parse_xlsx, data)
    existing = await existing_products(session, parsed)
    fields = ("article", "title", "price", "description", "available")
    return {
        "new_count": sum(row.article not in existing for row in parsed.rows),
        "errors": [{"row": e.row, "reason": e.reason} for e in parsed.errors],
        "conflicts": [{"article": row.article,
                       "current": {key: getattr(existing[row.article], key) for key in fields},
                       "incoming": row_json(row)} for row in parsed.rows if row.article in existing and differs(existing[row.article], row)],
    }


async def import_products(session: AsyncSession, data: bytes, choices: dict[str, str] | None = None) -> ImportResult:
    """Import selected values atomically; callers commit. Legacy callers keep existing products."""
    parsed = await asyncio.to_thread(parse_xlsx, data)
    existing = await existing_products(session, parsed)
    if choices is not None and any(choices.get(row.article) not in ("current", "xlsx") for row in parsed.rows if row.article in existing and differs(existing[row.article], row)):
        raise CatalogError("Выберите вариант для каждого совпавшего артикула. Если каталог изменился, загрузите файл заново", status=409)
    result = ImportResult(errors=list(parsed.errors))
    for row in parsed.rows:
        product = existing.get(row.article)
        if product is not None:
            if not differs(product, row) or choices is None or choices[row.article] == "current":
                result.skipped += 1
                continue
            for key in ("title", "price", "description", "available"):
                setattr(product, key, getattr(row, key))
            result.updated += 1
        else:
            session.add(Product(**row_json(row), photo_file_id=None))
            result.added += 1
    await session.flush()
    return result
