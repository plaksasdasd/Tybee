"""The /admin panel: catalog management (add/toggle/delete) and service stats.

Available only to admins in a private chat. The panel is a single editable
message navigated via inline callbacks (``adm:...``). Adding a product switches
the admin into :class:`UserState.ADMIN_ADD_PRODUCT`; their next message is parsed
by :func:`process_new_product` (wired from the text router).
"""
from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.config import get_settings
from app.db.models import MysteryBox, Order, Product, User
from app.keyboards import inline
from app.states import UserState

router = Router(name="admin_panel")

ARTICLE_RE = re.compile(r"^\d{6}$")


def _is_admin(user: User) -> bool:
    return user.is_admin or user.id in get_settings().admin_ids


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
    if not _is_admin(user):
        await message.answer(texts.ADMIN_ONLY)
        return
    await repo.set_state(session, user, UserState.MENU)
    await message.answer(texts.ADMIN_PANEL, reply_markup=inline.admin_panel())


@router.callback_query(F.data == "adm:panel:open")
async def panel_open(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not _is_admin(user):
        return await _deny(call)
    await repo.set_state(session, user, UserState.MENU)
    await _safe_edit(call, texts.ADMIN_PANEL, inline.admin_panel())
    await call.answer()


@router.callback_query(F.data == "adm:panel:close")
async def panel_close(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not _is_admin(user):
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
    if not _is_admin(user):
        return await _deny(call)
    await _render_products(call, session)
    await call.answer()


@router.callback_query(F.data == "adm:prod:add")
async def prod_add(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not _is_admin(user):
        return await _deny(call)
    await repo.set_state(session, user, UserState.ADMIN_ADD_PRODUCT)
    await _safe_edit(call, texts.ADMIN_ADD_PROMPT, inline.admin_add_cancel())
    await call.answer()


@router.callback_query(F.data.startswith("adm:prod:view:"))
async def prod_view(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not _is_admin(user):
        return await _deny(call)
    product = await repo.get_product(session, int(call.data.split(":")[3]))
    if product is None:
        await call.answer("Товар не найден", show_alert=True)
        return await _render_products(call, session)
    await _safe_edit(
        call,
        texts.ADMIN_PRODUCT_VIEW.format(
            id=product.id,
            article=product.article,
            title=product.title,
            price=product.price,
            available=_availability(product.available),
        ),
        inline.admin_product_view(product),
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm:prod:toggle:"))
async def prod_toggle(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not _is_admin(user):
        return await _deny(call)
    product = await repo.get_product(session, int(call.data.split(":")[3]))
    if product is None:
        await call.answer("Товар не найден", show_alert=True)
        return await _render_products(call, session)
    await repo.toggle_product(session, product)
    await _safe_edit(
        call,
        texts.ADMIN_PRODUCT_VIEW.format(
            id=product.id,
            article=product.article,
            title=product.title,
            price=product.price,
            available=_availability(product.available),
        ),
        inline.admin_product_view(product),
    )
    await call.answer("Обновлено")


@router.callback_query(F.data.startswith("adm:prod:del:"))
async def prod_delete(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not _is_admin(user):
        return await _deny(call)
    product = await repo.get_product(session, int(call.data.split(":")[3]))
    if product is not None:
        await repo.delete_product(session, product)
    await _render_products(call, session)
    await call.answer(texts.ADMIN_PRODUCT_DELETED)


@router.callback_query(F.data == "adm:box:list")
async def box_list(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not _is_admin(user):
        return await _deny(call)
    await _render_boxes(call, session)
    await call.answer()


@router.callback_query(F.data == "adm:box:add")
async def box_add(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not _is_admin(user):
        return await _deny(call)
    await repo.set_state(session, user, UserState.ADMIN_ADD_BOX)
    await _safe_edit(call, texts.ADMIN_ADD_BOX_PROMPT, inline.admin_add_box_cancel())
    await call.answer()


@router.callback_query(F.data.startswith("adm:box:view:"))
async def box_view(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not _is_admin(user):
        return await _deny(call)
    box = await repo.get_box(session, int(call.data.split(":")[3]))
    if box is None:
        await call.answer("Бокс не найден", show_alert=True)
        return await _render_boxes(call, session)
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
    await call.answer()


@router.callback_query(F.data.startswith("adm:box:toggle:"))
async def box_toggle(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not _is_admin(user):
        return await _deny(call)
    box = await repo.get_box(session, int(call.data.split(":")[3]))
    if box is None:
        await call.answer("Бокс не найден", show_alert=True)
        return await _render_boxes(call, session)
    await repo.toggle_box(session, box)
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
    await call.answer("Обновлено")


@router.callback_query(F.data.startswith("adm:box:del:"))
async def box_delete(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not _is_admin(user):
        return await _deny(call)
    box = await repo.get_box(session, int(call.data.split(":")[3]))
    if box is not None:
        await repo.delete_box(session, box)
    await _render_boxes(call, session)
    await call.answer(texts.ADMIN_BOX_DELETED)


@router.callback_query(F.data == "adm:stats")
async def stats(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not _is_admin(user):
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
    """Parse 'article title price' from a message and create a catalog product."""
    tokens = (message.text or "").split()
    if len(tokens) < 3 or not ARTICLE_RE.match(tokens[0]) or not tokens[-1].isdigit():
        await message.answer(texts.ADMIN_ADD_BAD_FORMAT, reply_markup=inline.admin_add_cancel())
        return
    article = tokens[0]
    price = int(tokens[-1])
    title = " ".join(tokens[1:-1])

    if await repo.get_product_by_article(session, article) is not None:
        await message.answer(
            texts.ADMIN_ADD_DUPLICATE.format(article=article),
            reply_markup=inline.admin_add_cancel(),
        )
        return

    await repo.create_product(session, article, title, price)
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
