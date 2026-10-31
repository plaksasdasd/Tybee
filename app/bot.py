from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BotCommand, BotCommandScopeChat

from app.avito_webhook import start_web_server
from app.config import get_settings
from app.db.base import init_models
from app.db.seed import purge_demo_products, seed
from app.handlers import build_router
from app.middlewares import DbSessionMiddleware
from app.services.avito import close_avito
from app.services.outbox import outbox_worker

logger = logging.getLogger(__name__)


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    middleware = DbSessionMiddleware()
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
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
    settings = get_settings()
    admin_commands = [
        BotCommand(command="start", description="Открыть меню Тайби"),
        BotCommand(command="admin", description="Админ-панель: каталог и статистика"),
        BotCommand(command="ban", description="Заблокировать: /ban id или @username"),
        BotCommand(command="pardon", description="Разблокировать: /pardon id или @username"),
    ]
    for admin_id in settings.admin_ids:
        try:
            await bot.set_my_commands(
                admin_commands, scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except TelegramBadRequest as exc:
            logger.info("Could not set commands for %s: %s", admin_id, exc)


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    await init_models()
    await seed()
    await purge_demo_products()

    bot = create_bot()
    dp = create_dispatcher()
    await _set_commands(bot)
    me = await bot.get_me()
    logger.info("Starting Тайби bot as @%s", me.username)

    outbox_task = asyncio.create_task(outbox_worker(bot))
    webhook_runner = await start_web_server()
    try:
        await dp.start_polling(bot)
    finally:
        outbox_task.cancel()
        with suppress(asyncio.CancelledError):
            await outbox_task
        if webhook_runner is not None:
            await webhook_runner.cleanup()
        await close_avito()
        await bot.session.close()
