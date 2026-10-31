from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BotCommand, BotCommandScopeChat

from support_bot.config import get_settings
from support_bot.db import init_models
from support_bot.handlers import build_router
from support_bot.middlewares import DbSessionMiddleware
from support_bot.repositories import seed_staff
from support_bot.source_db import source_enabled, source_engine
from support_bot.source_importer import source_importer_worker

logger = logging.getLogger(__name__)


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    middleware = DbSessionMiddleware()
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    dp.include_router(build_router())
    return dp


def create_bot() -> Bot:
    return Bot(
        token=get_settings().bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def _set_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="start", description="Открыть поддержку"),
        BotCommand(command="requests", description="Список обращений"),
    ]
    await bot.set_my_commands(commands)
    admin_commands = [
        *commands,
        BotCommand(command="admin", description="Сотрудники поддержки"),
        BotCommand(command="order", description="Выставить счёт клиенту"),
        BotCommand(command="ban", description="Заблокировать клиента"),
        BotCommand(command="pardon", description="Разблокировать клиента"),
    ]
    for admin_id in get_settings().admin_ids:
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
    if not get_settings().admin_ids:
        raise RuntimeError("Set at least one Telegram user id in SUPPORT_ADMIN_IDS")
    await init_models()
    await seed_staff()

    bot = create_bot()
    dp = create_dispatcher()
    await _set_commands(bot)
    me = await bot.get_me()
    logger.info("Starting support bot as @%s", me.username)
    importer_task = (
        asyncio.create_task(source_importer_worker(bot)) if source_enabled() else None
    )
    try:
        await dp.start_polling(bot)
    finally:
        if importer_task is not None:
            importer_task.cancel()
            with suppress(asyncio.CancelledError):
                await importer_task
        if source_engine is not None:
            await source_engine.dispose()
        await bot.session.close()
