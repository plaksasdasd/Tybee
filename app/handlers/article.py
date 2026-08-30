from __future__ import annotations

import html
import re

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.assets import Banner
from app.db.models import OrderStatus, OrderType, User
from app.keyboards import inline
from app.services.navigation import show_screen
from app.states import UserState

router = Router(name="article")

ARTICLE_RE = re.compile(r"^\d{6}$")


@router.callback_query(F.data == "order:start")
async def order_start(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    await repo.set_state(session, user, UserState.MENU)
    await show_screen(
        bot, session, user, Banner.ORDER_CHOICE, texts.ORDER_CHOICE, inline.order_choice()
    )
    await call.answer()


@router.callback_query(F.data == "order:article")
async def order_article(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    await repo.set_state(session, user, UserState.MENU)
    await show_screen(
        bot, session, user, Banner.ARTICLE, texts.ARTICLE_ALREADY, inline.article_choice()
    )
    await call.answer()


@router.callback_query(F.data == "order:has_article")
async def ask_article(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    await repo.set_state(session, user, UserState.AWAIT_ARTICLE)
    await show_screen(
        bot,
        session,
        user,
        Banner.ARTICLE,
        texts.ARTICLE_PROMPT,
        inline.back_button("order:article"),
    )
    await call.answer()


async def _offer_product(bot: Bot, session: AsyncSession, user: User, product) -> None:
    """Create an article order for a catalog product and show the payment offer."""
    await repo.set_state(session, user, UserState.MENU)
    order = await repo.create_order(
        session,
        user,
        type=OrderType.ARTICLE.value,
        status=OrderStatus.AWAITING_PAYMENT.value,
        article=product.article,
        title=product.title,
        price=product.price,
    )
    description = f"📝 {html.escape(product.description)}\n" if product.description else ""
    caption = texts.FOUND.format(
        title=html.escape(product.title),
        code=product.article,
        price=product.price,
        description=description,
    )
    await show_screen(
        bot,
        session,
        user,
        Banner.APPROVED,
        caption,
        inline.pay_offer(order.id),
        force_new=True,
        photo=product.photo_file_id,
    )


async def process_article(bot: Bot, session: AsyncSession, user: User, code: str) -> None:
    """Validate an article code, search the catalog and route accordingly."""
    code = code.strip()
    if not ARTICLE_RE.match(code):
        await show_screen(
            bot,
            session,
            user,
            Banner.ARTICLE,
            texts.ARTICLE_INVALID,
            inline.back_button("order:article"),
            force_new=True,
        )
        return

    product = await repo.find_product_by_article(session, code)
    if product is None:
        await repo.set_state(session, user, UserState.MENU)
        await show_screen(
            bot,
            session,
            user,
            Banner.REJECTED,
            texts.REJECTED_CATALOG.format(code=code),
            inline.catalog_rejected(),
            force_new=True,
        )
        return

    await _offer_product(bot, session, user, product)
