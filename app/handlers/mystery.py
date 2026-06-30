from __future__ import annotations

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

router = Router(name="mystery")


@router.callback_query(F.data == "order:mystery")
async def show_boxes(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    await repo.set_state(session, user, UserState.MENU)
    boxes = await repo.list_available_boxes(session)
    if boxes:
        await show_screen(
            bot, session, user, Banner.ARTICLE, texts.MYSTERY_LIST, inline.mystery_boxes(boxes)
        )
    else:
        await show_screen(
            bot, session, user, Banner.ARTICLE, texts.MYSTERY_EMPTY, inline.mystery_empty()
        )
    await call.answer()


@router.callback_query(F.data.startswith("mystery:item:"))
async def pick_box(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User) -> None:
    box = await repo.get_box(session, int(call.data.split(":")[2]))
    if box is None or not box.available:
        await call.answer("Бокс недоступен", show_alert=True)
        await show_boxes(call, bot, session, user)
        return
    await repo.set_state(session, user, UserState.MENU)
    order = await repo.create_order(
        session,
        user,
        type=OrderType.MYSTERY_BOX.value,
        status=OrderStatus.AWAITING_PAYMENT.value,
        title=box.title,
        price=box.price,
    )
    caption = texts.MYSTERY_OFFER.format(title=box.title, price=box.price)
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Перейти к оплате", callback_data=f"pay:proceed:{order.id}")
    kb.button(text=texts.BTN_MENU, callback_data="menu:open")
    kb.adjust(1)
    await show_screen(
        bot, session, user, Banner.APPROVED, caption, kb.as_markup(), force_new=True
    )
    await call.answer()
