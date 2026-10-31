"""The /admin panel: catalog and service stats.

Available only to admins in a private chat. The panel is a single editable
message navigated via inline callbacks (``adm:...``). Free-form additions are
parsed from the admin's next message via the text router.
"""
from __future__ import annotations

import html
import logging
import os
import tempfile

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.db.models import MysteryBox, Order, Product, User
from app.handlers.article import ARTICLE_RE
from app.keyboards import inline
from app.states import UserState

logger = logging.getLogger(__name__)

router = Router(name="admin_panel")


def _availability(value: bool) -> str:
    return "да 🟢" if value else "нет 🔴"


async def _deny(call: CallbackQuery) -> None:
    await call.answer(texts.ADMIN_ONLY, show_alert=True)


async def _safe_edit(call: CallbackQuery, text: str, kb: InlineKeyboardMarkup) -> None:
    if call.message is None:
        return
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass


async def _show_product_view(call: CallbackQuery, product: Product) -> None:
    await _safe_edit(
        call,
        texts.ADMIN_PRODUCT_VIEW.format(
            id=product.id,
            article=product.article,
            title=html.escape(product.title),
            price=product.price,
            description=html.escape(product.description or "—"),
            photo=_availability(product.photo_file_id is not None),
            available=_availability(product.available),
        ),
        inline.admin_product_view(product),
    )


async def _show_box_view(call: CallbackQuery, box: MysteryBox) -> None:
    await _safe_edit(
        call,
        texts.ADMIN_BOX_VIEW.format(
            id=box.id,
            title=box.title,
            price=box.price,
            available=_availability(box.available),
        ),
        inline.admin_box_view(box),
    )


PRODUCTS_PER_PAGE = 10


async def _render_products(call: CallbackQuery, session: AsyncSession, page: int = 0) -> None:
    products = await repo.list_products(session)
    if not products:
        await _safe_edit(call, texts.ADMIN_PRODUCTS_EMPTY, inline.admin_products(products, page=0))
        return
    total_pages = (len(products) + PRODUCTS_PER_PAGE - 1) // PRODUCTS_PER_PAGE or 1
    page = max(0, min(page, total_pages - 1))
    text = texts.ADMIN_PRODUCTS_HEADER.format(count=len(products))
    text += f"\nСтр. {page + 1}/{total_pages}"
    await _safe_edit(call, text, inline.admin_products(products, page=page))


async def _render_boxes(call: CallbackQuery, session: AsyncSession) -> None:
    boxes = await repo.list_boxes(session)
    text = (
        texts.ADMIN_BOXES_HEADER.format(count=len(boxes))
        if boxes
        else texts.ADMIN_BOXES_EMPTY
    )
    await _safe_edit(call, text, inline.admin_boxes(boxes))


@router.message(Command("admin"), F.chat.type == "private")
async def open_admin(message: Message, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        await message.answer(texts.ADMIN_ONLY)
        return
    await repo.set_state(session, user, UserState.MENU)
    await message.answer(texts.ADMIN_PANEL, reply_markup=inline.admin_panel())


@router.callback_query(F.data == "adm:panel:open")
async def panel_open(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        return await _deny(call)
    await repo.set_state(session, user, UserState.MENU)
    await _safe_edit(call, texts.ADMIN_PANEL, inline.admin_panel())
    await call.answer()


@router.callback_query(F.data == "adm:panel:close")
async def panel_close(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        return await _deny(call)
    await repo.set_state(session, user, UserState.MENU)
    if call.message is not None:
        try:
            await call.message.delete()
        except TelegramBadRequest:
            pass
    await call.answer("Закрыто")


@router.callback_query(F.data.startswith("adm:prod:list"))
async def prod_list(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        return await _deny(call)
    # adm:prod:list  or adm:prod:list:<page>
    page = 0
    parts = call.data.split(":")
    if len(parts) == 4:
        try:
            page = int(parts[3])
        except ValueError:
            page = 0
    await _render_products(call, session, page=page)
    await call.answer()


@router.callback_query(F.data == "adm:prod:nop")
async def prod_nop(call: CallbackQuery) -> None:
    await call.answer()


@router.callback_query(F.data == "adm:prod:add")
async def prod_add(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        return await _deny(call)
    await repo.set_state(session, user, UserState.ADMIN_ADD_PRODUCT)
    await _safe_edit(call, texts.ADMIN_ADD_PROMPT, inline.admin_add_cancel())
    await call.answer()


@router.callback_query(F.data == "adm:prod:import")
async def prod_import(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        return await _deny(call)
    await repo.set_state(session, user, UserState.ADMIN_IMPORT_PRODUCTS)
    await _safe_edit(call, texts.ADMIN_IMPORT_PROMPT, inline.admin_import_cancel())
    await call.answer()


@router.callback_query(F.data.startswith("adm:prod:view:"))
async def prod_view(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        return await _deny(call)
    product = await repo.get_product(session, int(call.data.split(":")[3]))
    if product is None:
        await call.answer("Артикул не найден", show_alert=True)
        return await _render_products(call, session)
    await _show_product_view(call, product)
    await call.answer()


@router.callback_query(F.data.startswith("adm:prod:toggle:"))
async def prod_toggle(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        return await _deny(call)
    product = await repo.get_product(session, int(call.data.split(":")[3]))
    if product is None:
        await call.answer("Артикул не найден", show_alert=True)
        return await _render_products(call, session)
    await repo.toggle_product(session, product)
    await _show_product_view(call, product)
    await call.answer("Обновлено")


@router.callback_query(F.data.startswith("adm:prod:del:"))
async def prod_delete(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        return await _deny(call)
    product = await repo.get_product(session, int(call.data.split(":")[3]))
    if product is not None:
        await repo.delete_product(session, product)
    await _render_products(call, session)
    await call.answer(texts.ADMIN_PRODUCT_DELETED)


@router.callback_query(F.data == "adm:box:list")
async def box_list(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        return await _deny(call)
    await _render_boxes(call, session)
    await call.answer()


@router.callback_query(F.data == "adm:box:add")
async def box_add(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        return await _deny(call)
    await repo.set_state(session, user, UserState.ADMIN_ADD_BOX)
    await _safe_edit(call, texts.ADMIN_ADD_BOX_PROMPT, inline.admin_add_box_cancel())
    await call.answer()


@router.callback_query(F.data.startswith("adm:box:view:"))
async def box_view(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        return await _deny(call)
    box = await repo.get_box(session, int(call.data.split(":")[3]))
    if box is None:
        await call.answer("Бокс не найден", show_alert=True)
        return await _render_boxes(call, session)
    await _show_box_view(call, box)
    await call.answer()


@router.callback_query(F.data.startswith("adm:box:toggle:"))
async def box_toggle(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        return await _deny(call)
    box = await repo.get_box(session, int(call.data.split(":")[3]))
    if box is None:
        await call.answer("Бокс не найден", show_alert=True)
        return await _render_boxes(call, session)
    await repo.toggle_box(session, box)
    await _show_box_view(call, box)
    await call.answer("Обновлено")


@router.callback_query(F.data.startswith("adm:box:del:"))
async def box_delete(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        return await _deny(call)
    box = await repo.get_box(session, int(call.data.split(":")[3]))
    if box is not None:
        await repo.delete_box(session, box)
    await _render_boxes(call, session)
    await call.answer(texts.ADMIN_BOX_DELETED)


@router.callback_query(F.data == "adm:stats")
async def stats(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        return await _deny(call)
    users = await repo.count_rows(session, User)
    banned = await repo.count_banned_users(session)
    products = await repo.count_rows(session, Product)
    boxes = await repo.count_rows(session, MysteryBox)
    orders = await repo.count_rows(session, Order)
    by_status = await repo.orders_by_status(session)
    status_lines = "\n".join(
        f"  • {texts.STATUS_LABELS.get(status, status)}: {count}"
        for status, count in sorted(by_status.items())
    )
    await _safe_edit(
        call,
        texts.ADMIN_STATS.format(
            users=users,
            banned=banned,
            products=products,
            boxes=boxes,
            orders=orders,
            by_status=status_lines,
        ),
        inline.admin_back(),
    )
    await call.answer()


async def process_new_product(message: Message, session: AsyncSession, user: User) -> None:
    """Create a catalog article from an admin message.

    Expected format (text or photo caption):
    first line — ``article title price``; the following lines, if any, become
    the product description. An attached photo is stored and shown to clients.
    """
    raw = message.text or message.caption or ""
    first_line, _, rest = raw.partition("\n")
    tokens = first_line.split()
    if len(tokens) < 3 or not ARTICLE_RE.match(tokens[0]) or not tokens[-1].isdigit():
        await message.answer(texts.ADMIN_ADD_BAD_FORMAT, reply_markup=inline.admin_add_cancel())
        return
    article = tokens[0]
    price = int(tokens[-1])
    title = " ".join(tokens[1:-1])
    description = rest.strip() or None
    photo_file_id = message.photo[-1].file_id if message.photo else None

    if await repo.get_product_by_article(session, article) is not None:
        await message.answer(
            texts.ADMIN_ADD_DUPLICATE.format(article=article),
            reply_markup=inline.admin_add_cancel(),
        )
        return

    await repo.create_product(
        session, article, title, price, description=description, photo_file_id=photo_file_id
    )
    await repo.set_state(session, user, UserState.MENU)
    await message.answer(
        texts.ADMIN_ADD_DONE.format(title=html.escape(title), article=article, price=price)
    )
    products = await repo.list_products(session)
    # Show paginated list (page 0) — important when catalog is large (>10)
    header = texts.ADMIN_PRODUCTS_HEADER.format(count=len(products))
    if len(products) > PRODUCTS_PER_PAGE:
        header += f"\nСтр. 1/{(len(products) + PRODUCTS_PER_PAGE - 1)//PRODUCTS_PER_PAGE}"
    await message.answer(
        header,
        reply_markup=inline.admin_products(products, page=0),
    )


async def process_new_box(message: Message, session: AsyncSession, user: User) -> None:
    """Parse 'title price' from a message and create a Mystery Box variant."""
    tokens = (message.text or "").split()
    if len(tokens) < 2 or not tokens[-1].isdigit():
        await message.answer(
            texts.ADMIN_ADD_BOX_BAD_FORMAT, reply_markup=inline.admin_add_box_cancel()
        )
        return
    price = int(tokens[-1])
    title = " ".join(tokens[:-1])

    await repo.create_box(session, title, price)
    await repo.set_state(session, user, UserState.MENU)
    await message.answer(
        texts.ADMIN_ADD_BOX_DONE.format(title=html.escape(title), price=price)
    )
    boxes = await repo.list_boxes(session)
    await message.answer(
        texts.ADMIN_BOXES_HEADER.format(count=len(boxes)),
        reply_markup=inline.admin_boxes(boxes),
    )


def _parse_available(value: object | None) -> bool:
    if value is None or value == "":
        return True
    s = str(value).strip().lower()
    if s in ("1", "true", "да", "yes", "y", "+", "available", "в наличии", "on", "вкл"):
        return True
    if s in ("0", "false", "нет", "no", "n", "-", "off", "недоступен", "выкл"):
        return False
    return True


def _norm_article(value: object | None) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    # Excel numeric articles like 482915.0 -> "482915"
    if s.endswith(".0"):
        s = s[:-2]
    # Remove spaces
    s = s.replace(" ", "").replace("'", "")
    return s


@router.message(F.document, F.chat.type == "private")
async def process_import_document(
    message: Message, bot: Bot, session: AsyncSession, user: User
) -> None:
    if not repo.is_admin(user):
        return
    if user.state != UserState.ADMIN_IMPORT_PRODUCTS.value:
        return
    doc = message.document
    if doc is None:
        return
    filename = (doc.file_name or "").lower()
    if not filename.endswith((".xlsx", ".xls")):
        await message.answer(texts.ADMIN_IMPORT_BAD_FILE, reply_markup=inline.admin_import_cancel())
        return

    # Download to temp file
    tmp_path = ""
    try:
        file = await bot.get_file(doc.file_id)
        if file.file_path is None:
            raise RuntimeError("file_path is None")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp_path = tmp.name
        await bot.download_file(file.file_path, tmp_path)
    except Exception as exc:
        logger.exception("Failed to download xlsx: %s", exc)
        await message.answer(texts.ADMIN_IMPORT_BAD_FILE, reply_markup=inline.admin_import_cancel())
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return

    # Parse xlsx
    try:
        import openpyxl  # type: ignore
    except ImportError:
        logger.error("openpyxl not installed")
        await message.answer("❌ На сервере не установлен openpyxl. Добавьте в requirements.txt", reply_markup=inline.admin_import_cancel())
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return

    added = 0
    skipped = 0
    errors = 0
    error_lines: list[str] = []

    try:
        wb = openpyxl.load_workbook(tmp_path, data_only=True, read_only=True)
        ws = wb.active
        if ws is None:
            raise ValueError("Empty workbook")
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            await message.answer(texts.ADMIN_IMPORT_BAD_FILE, reply_markup=inline.admin_import_cancel())
            return

        # Header-aware column mapping — поддерживает и "Артикул|Название|Цена|Описание" и "SKU|NAME|Text|Price|Photo"
        start_idx = 0
        article_idx, title_idx, price_idx, desc_idx, avail_idx = 0, 1, 2, 3, 4
        if rows and rows[0]:
            header = rows[0]
            header_lower = [str(c or "").strip().lower() for c in header]
            # Check if first row looks like header
            combined_header = " ".join(header_lower)
            has_header_keywords = any(
                k in combined_header for k in ("артикул", "sku", "article", "название", "name", "title", "цена", "price", "опис", "text", "desc")
            )
            # Also treat as header if article cell not 6 digits but contains letters
            first_article_norm = _norm_article(header[0]) if len(header) > 0 else ""
            is_header = has_header_keywords or not ARTICLE_RE.match(first_article_norm)
            # Only skip first row if it actually looks like header and second row looks like data
            if is_header:
                # Verify second row looks like data (article 6 digits) to avoid false positive on header-less files where first product title is text
                looks_like_header = has_header_keywords
                if not looks_like_header and len(rows) > 1 and rows[1]:
                    second_art = _norm_article(rows[1][0]) if len(rows[1]) > 0 else ""
                    looks_like_header = bool(ARTICLE_RE.match(second_art))
                if looks_like_header:
                    # Map columns by header names
                    def _find_idx(keywords: list[str]) -> int | None:
                        for i, h in enumerate(header_lower):
                            if any(k in h for k in keywords):
                                return i
                        return None

                    a_idx = _find_idx(["артикул", "sku", "article", "код"])
                    t_idx = _find_idx(["название", "name", "title"])
                    p_idx = _find_idx(["цена", "price", "стоимость"])
                    d_idx = _find_idx(["опис", "text", "desc"])
                    av_idx = _find_idx(["доступ", "available", "актив", "в наличии"])

                    # Fallback defaults if not found
                    article_idx = a_idx if a_idx is not None else 0
                    title_idx = t_idx if t_idx is not None else 1
                    # price fallback
                    if p_idx is not None:
                        price_idx = p_idx
                    else:
                        # if header has 5 cols like SKU,NAME,Text,Price,Photo -> price at 3, desc at 2
                        price_idx = 2
                    if d_idx is not None:
                        desc_idx = d_idx
                    else:
                        # Find first unused index for description
                        used = {article_idx, title_idx, price_idx}
                        if av_idx is not None:
                            used.add(av_idx)
                        desc_idx = None
                        for i in range(len(header)):
                            if i not in used:
                                desc_idx = i
                                break
                        if desc_idx is None:
                            desc_idx = 3
                    avail_idx = av_idx  # may be None -> ignore
                    start_idx = 1

        # Limit — увеличил до 2000 для больших каталогов (файл ебатория ~605 строк)
        max_rows = 2000
        data_rows = rows[start_idx : start_idx + max_rows]

        for idx, row in enumerate(data_rows, start=start_idx + 1):
            if row is None:
                continue
            # Skip fully empty rows
            if all(c is None or str(c).strip() == "" for c in row):
                continue
            # Safe get by index
            def _cell(i: int | None) -> object | None:
                if i is None or i >= len(row):
                    return None
                return row[i]

            article_raw = _cell(article_idx)
            title_raw = _cell(title_idx)
            price_raw = _cell(price_idx)
            desc_raw = _cell(desc_idx)
            avail_raw = _cell(avail_idx) if avail_idx is not None else None

            article = _norm_article(article_raw)
            title = str(title_raw or "").strip()
            price_str = str(price_raw or "").strip() if price_raw is not None else ""
            # price may be float 6900.0
            if price_str.endswith(".0"):
                price_str = price_str[:-2]
            description = str(desc_raw or "").strip() or None
            available = _parse_available(avail_raw)

            # Validation
            if not article or not ARTICLE_RE.match(article):
                errors += 1
                if len(error_lines) < 10:
                    error_lines.append(texts.ADMIN_IMPORT_ERROR_DETAIL.format(row=idx, reason=f"артикул '{article}' — нужны 6 цифр"))
                continue
            if not title:
                errors += 1
                if len(error_lines) < 10:
                    error_lines.append(texts.ADMIN_IMPORT_ERROR_DETAIL.format(row=idx, reason="пустое название"))
                continue
            # price parsing: allow int, float, string with spaces
            try:
                # Remove spaces and commas
                p_clean = price_str.replace(" ", "").replace(",", ".").replace("₽", "").strip()
                price_val = int(float(p_clean))
                if price_val <= 0:
                    raise ValueError
            except Exception:
                errors += 1
                if len(error_lines) < 10:
                    error_lines.append(texts.ADMIN_IMPORT_ERROR_DETAIL.format(row=idx, reason=f"цена '{price_raw}' — нужно число"))
                continue

            if len(title) > 256:
                title = title[:256]
            if description and len(description) > 2000:
                description = description[:2000]

            # Duplicate check
            existing = await repo.get_product_by_article(session, article)
            if existing is not None:
                skipped += 1
                continue

            await repo.create_product(session, article, title, price_val, description=description, photo_file_id=None)
            # Respect available flag: if False -> toggle
            if not available:
                # newly created available=True by default, toggle to False
                prod = await repo.get_product_by_article(session, article)
                if prod is not None and prod.available:
                    await repo.toggle_product(session, prod)
            added += 1

        # If more rows than max, note
        if len(rows) - start_idx > max_rows:
            error_lines.append(f"• Показаны первые {max_rows} строк, остальные проигнорированы (лимит {max_rows})\n")

    except Exception as exc:
        logger.exception("Failed to parse xlsx: %s", exc)
        await message.answer(texts.ADMIN_IMPORT_BAD_FILE, reply_markup=inline.admin_import_cancel())
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    await repo.set_state(session, user, UserState.MENU)
    error_details = "".join(error_lines)
    if error_details:
        error_details = "\nДетали:\n" + error_details
    await message.answer(
        texts.ADMIN_IMPORT_RESULT.format(added=added, skipped=skipped, errors=errors, error_details=error_details)
    )
    products = await repo.list_products(session)
    header = texts.ADMIN_PRODUCTS_HEADER.format(count=len(products))
    if len(products) > PRODUCTS_PER_PAGE:
        header += f"\nСтр. 1/{(len(products) + PRODUCTS_PER_PAGE - 1)//PRODUCTS_PER_PAGE}"
    await message.answer(
        header,
        reply_markup=inline.admin_products(products, page=0),
    )



