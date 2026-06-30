from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
)

from app.config import get_settings
from app.db.base import init_models
from app.db.seed import seed
from app.handlers import build_router
from app.middlewares import DbSessionMiddleware

logger = logging.getLogger(__name__)


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    middleware = DbSessionMiddleware()
    dp.message.middleware(middleware)
    dp.callback_query.middleware(middleware)
    dp.include_router(build_router())
    return dp


def create_bot() -> Bot:
    settings = get_settings()
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def _set_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [BotCommand(command="start", description="Открыть меню Тайби")]
    )
    # Expose support commands only to admins / the support chat.
    settings = get_settings()
    admin_commands = [
        BotCommand(command="order", description="Создать заказ: /order название описание сумма"),
        BotCommand(command="ban", description="Заблокировать клиента"),
        BotCommand(command="pardon", description="Разблокировать клиента"),
    ]
    scope_chats = list(settings.admin_ids)
    if settings.support_chat_id is not None:
        scope_chats.append(settings.support_chat_id)
    for chat_id in scope_chats:
        try:
            await bot.set_my_commands(
                admin_commands, scope=BotCommandScopeChat(chat_id=chat_id)
            )
        except TelegramBadRequest as exc:
            logger.info("Could not set admin commands for %s: %s", chat_id, exc)


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    await init_models()
    await seed()

    bot = create_bot()
    dp = create_dispatcher()
    await _set_commands(bot)

    me = await bot.get_me()
    logger.info("Starting Тайби bot as @%s", me.username)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
