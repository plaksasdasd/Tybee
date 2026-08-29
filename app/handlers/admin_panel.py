"""The /admin panel: article catalog management (add/toggle/delete) and service stats.

Available only to admins in a private chat. The panel is a single editable
message navigated via inline callbacks (``adm:...``). Adding an article switches
the admin into :class:`UserState.ADMIN_ADD_PRODUCT`; their next message is parsed
by :func:`process_new_product` (wired from the text router).
"""
from __future__ import annotations

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
            title=product.title,
            price=product.price,
            description=product.description or "—",
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


async def _render_products(call: CallbackQuery, session: AsyncSession) -> None:
    products = await repo.list_products(session)
    text = (
        texts.ADMIN_PRODUCTS_HEADER.format(count=len(products))
        if products
        else texts.ADMIN_PRODUCTS_EMPTY
    )
    await _safe_edit(call, text, inline.admin_products(products))


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


@router.callback_query(F.data == "adm:prod:list")
async def prod_list(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not repo.is_admin(user):
        return await _deny(call)
    await _render_products(call, session)
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
        texts.ADMIN_ADD_DONE.format(title=title, article=article, price=price)
    )
    products = await repo.list_products(session)
    await message.answer(
        texts.ADMIN_PRODUCTS_HEADER.format(count=len(products)),
        reply_markup=inline.admin_products(products),
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
    await message.answer(texts.ADMIN_ADD_BOX_DONE.format(title=title, price=price))
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


def _is_header_row(article: str, title: str, price: str) -> bool:
    a = article.strip().lower()
    t = title.strip().lower()
    p = price.strip().lower()
    headers = {"артикул", "article", "код", "id", "название", "title", "цена", "price", "стоимость"}
    return a in headers or t in headers or p in headers


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

        # Detect header
        start_idx = 0
        if rows:
            first = rows[0]
            if first and len(first) >= 2:
                a0 = _norm_article(first[0])
                t0 = str(first[1] or "").strip()
                p0 = str(first[2] or "").strip() if len(first) > 2 else ""
                if _is_header_row(a0, t0, p0) or not ARTICLE_RE.match(a0):
                    # If first row is header or article invalid and contains header words, skip it
                    # Check if any header word present
                    combined = " ".join([str(c or "").lower() for c in first[:4]])
                    if any(h in combined for h in ("артикул", "article", "название", "title", "цена", "price")):
                        start_idx = 1
                    elif not ARTICLE_RE.match(a0):
                        # Could be header without keywords but still invalid article -> treat as header if not digit
                        # only skip if first row article not 6 digits and second row looks like data
                        if len(rows) > 1 and rows[1] and _norm_article(rows[1][0]) and ARTICLE_RE.match(_norm_article(rows[1][0])):
                            start_idx = 1

        # Limit
        max_rows = 500
        data_rows = rows[start_idx : start_idx + max_rows]

        for idx, row in enumerate(data_rows, start=start_idx + 1):
            if row is None:
                continue
            # Skip fully empty rows
            if all(c is None or str(c).strip() == "" for c in row):
                continue
            # Pad to 5 cols
            vals = list(row) + [None] * 5
            article_raw = vals[0]
            title_raw = vals[1]
            price_raw = vals[2]
            desc_raw = vals[3] if len(vals) > 3 else None
            avail_raw = vals[4] if len(vals) > 4 else None

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
            error_lines.append(f"• Показаны первые {max_rows} строк, остальные проигнорированы (лимит 500)\n")

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
    await message.answer(
        texts.ADMIN_PRODUCTS_HEADER.format(count=len(products)),
        reply_markup=inline.admin_products(products),
    )



