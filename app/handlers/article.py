from __future__ import annotations

import re

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
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
        bot, session, user, Banner.ARTICLE, texts.ARTICLE_ALREADY, inline.article_choice()
    )
    await call.answer()


@router.callback_query(F.data == "order:has_article")
async def ask_article(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    await repo.set_state(session, user, UserState.AWAIT_ARTICLE)
    await show_screen(
        bot, session, user, Banner.ARTICLE, texts.ARTICLE_PROMPT, inline.cancel_to_menu()
    )
    await call.answer()


async def process_article(bot: Bot, session: AsyncSession, user: User, code: str) -> None:
    """Validate an article code, search the catalog and route accordingly."""
    code = code.strip()
    if not ARTICLE_RE.match(code):
        await show_screen(
            bot, session, user, Banner.ARTICLE, texts.ARTICLE_INVALID, inline.cancel_to_menu()
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
        )
        return

    await repo.set_state(session, user, UserState.MENU)
    order = await repo.create_order(
        session,
        user,
        type=OrderType.ARTICLE.value,
        status=OrderStatus.AWAITING_PAYMENT.value,
        article=code,
        title=product.title,
        price=product.price,
    )
    caption = texts.FOUND.format(title=product.title, code=code, price=product.price)
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Перейти к оплате", callback_data=f"pay:proceed:{order.id}")
    kb.button(text=texts.BTN_MENU, callback_data="menu:open")
    kb.adjust(1)
    await show_screen(bot, session, user, Banner.APPROVED, caption, kb.as_markup())
