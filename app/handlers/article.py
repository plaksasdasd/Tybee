from __future__ import annotations

import html

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.assets import Banner
from app.catalog.common import ARTICLE_RE
from app.covers.loader import cover_text, _visible_length
from app.db.models import Cover, OrderStatus, OrderType, User, UserCover
from app.keyboards import inline
from app.services.navigation import show_screen
from app.services.product_photos import get_product_photo
from app.states import UserState

router = Router(name="article")


@router.callback_query(F.data == "order:start")
async def order_start(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, uc: UserCover
) -> None:
    await call.answer()  # stop the button spinner before the slow screen edit
    await repo.set_state(session, uc, UserState.MENU)
    cid = uc.cover_id
    await show_screen(
        bot,
        session,
        user,
        uc,
        Banner.ORDER_CHOICE,
        cover_text(cid, "ORDER_CHOICE"),
        inline.order_choice(cid),
    )


@router.callback_query(F.data == "order:article")
async def order_article(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, uc: UserCover
) -> None:
    await call.answer()  # stop the button spinner before the slow screen edit
    await repo.set_state(session, uc, UserState.MENU)
    cid = uc.cover_id
    await show_screen(
        bot,
        session,
        user,
        uc,
        Banner.ARTICLE,
        cover_text(cid, "ARTICLE_ALREADY"),
        inline.article_choice(cid),
    )


@router.callback_query(F.data == "order:has_article")
async def ask_article(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, uc: UserCover
) -> None:
    await call.answer()  # stop the button spinner before the slow screen edit
    await repo.set_state(session, uc, UserState.AWAIT_ARTICLE)
    cid = uc.cover_id
    await show_screen(
        bot,
        session,
        user,
        uc,
        Banner.ARTICLE,
        cover_text(cid, "ARTICLE_PROMPT"),
        inline.back_button(cid, "order:article"),
    )


async def _offer_product(
    bot: Bot, session: AsyncSession, user: User, uc: UserCover, cover: Cover, product
) -> None:
    """Create an article order for a catalog product and show the payment offer."""
    await repo.set_state(session, uc, UserState.MENU)
    cid = uc.cover_id
    order = await repo.create_order(
        session,
        user,
        uc,
        type=OrderType.ARTICLE.value,
        status=OrderStatus.AWAITING_PAYMENT.value,
        article=product.article,
        title=product.title,
        price=product.price,
    )
    description = f"📝 {html.escape(product.description)}\n" if product.description else ""
    caption = cover_text(
        cid,
        "FOUND",
        title=html.escape(product.title),
        code=product.article,
        price=product.price,
        description=description,
    )
    if _visible_length(caption) > 1024 and product.description:
        caption = cover_text(cid, "FOUND", title=html.escape(product.title), code=product.article,
                             price=product.price, description="")
        await bot.send_message(chat_id=user.id, text=f"📝 {html.escape(product.description)}", parse_mode="HTML")
    await show_screen(
        bot,
        session,
        user,
        uc,
        Banner.APPROVED,
        caption,
        inline.pay_offer(cid, order.id),
        force_new=True,
        photo=await get_product_photo(session, product, cover),
        product_id=product.id,
    )


async def process_article(
    bot: Bot, session: AsyncSession, user: User, uc: UserCover, cover: Cover, code: str
) -> None:
    """Validate an article code, search the catalog and route accordingly."""
    cid = uc.cover_id
    code = code.strip()
    if not ARTICLE_RE.match(code):
        await show_screen(
            bot,
            session,
            user,
            uc,
            Banner.ARTICLE,
            cover_text(cid, "ARTICLE_INVALID"),
            inline.back_button(cid, "order:article"),
            force_new=True,
        )
        return

    product = await repo.find_product_by_article(session, code)
    if product is None:
        await repo.set_state(session, uc, UserState.MENU)
        await show_screen(
            bot,
            session,
            user,
            uc,
            Banner.REJECTED,
            cover_text(cid, "REJECTED_CATALOG", code=code),
            inline.catalog_rejected(cid),
            force_new=True,
        )
        return

    await _offer_product(bot, session, user, uc, cover, product)
