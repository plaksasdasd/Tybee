from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
)

from app.config import get_settings
from app.db.base import init_models
from app.db.seed import purge_demo_products, seed
from app.handlers import build_router
from app.middlewares import DbSessionMiddleware

logger = logging.getLogger(__name__)


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    middleware = DbSessionMiddleware()
    # Outer (not inner) middlewares run for EVERY update, before filters —
    # required here: the ban guard and the per-update user must not depend on
    # some handler's filters matching first.
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
    # Support commands belong in the support chat (their handlers are scoped there).
    support_commands = [
        BotCommand(command="order", description='Создать заказ: /order "Название" [описание] сумма'),
        BotCommand(command="ban", description="Заблокировать клиента"),
        BotCommand(command="pardon", description="Разблокировать клиента"),
    ]
    # The admin panel is a private-chat command for administrators.
    admin_private_commands = [
        BotCommand(command="start", description="Открыть меню Тайби"),
        BotCommand(command="admin", description="Админ-панель: каталог и операторы"),
        BotCommand(command="panel", description="Открыть веб-панель поддержки"),
        BotCommand(command="ban", description="Заблокировать: /ban id или @username"),
        BotCommand(command="pardon", description="Разблокировать: /pardon id или @username"),
    ]
    scopes: list[tuple[int, list[BotCommand]]] = [
        (admin_id, admin_private_commands) for admin_id in settings.admin_ids
    ]
    if settings.support_chat_id is not None:
        scopes.append((settings.support_chat_id, support_commands))
    for chat_id, commands in scopes:
        try:
            await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=chat_id))
        except TelegramBadRequest as exc:
            logger.info("Could not set commands for %s: %s", chat_id, exc)


async def _check_support_chat(bot: Bot, me_id: int) -> None:
    """Verify the support-chat setup and log obvious misconfigurations.

    Per-client forum topics are created only when the support chat is a forum
    supergroup and the bot is an admin with the right to manage topics —
    without that the "dialogs" never appear in the group.
    """
    settings = get_settings()
    chat_id = settings.support_chat_id
    if chat_id is None:
        logger.info(
            "SUPPORT_CHAT_ID is not set — client dialogs go to the first admin's DMs."
        )
        return
    try:
        chat = await bot.get_chat(chat_id)
        logger.info("Support chat: id=%s type=%s title=%s", chat_id, chat.type, chat.title)
        is_forum = getattr(chat, "is_forum", None)
        if not is_forum:
            logger.warning(
                "Support chat %s is NOT a forum supergroup (is_forum=%s). "
                "Per-client topics (dialogs) will NOT be created — messages will "
                "go to the general chat. Enable Topics in the group settings.",
                chat_id,
                is_forum,
            )
        member = await bot.get_chat_member(chat_id, me_id)
        status = getattr(member, "status", None)
        if status != "administrator":
            logger.warning(
                "The bot is NOT an administrator of the support chat %s (status=%s). "
                "It cannot create topics or post messages into them.",
                chat_id,
                status,
            )
        elif not getattr(member, "can_manage_topics", False):
            logger.warning(
                "The bot lacks the 'manage topics' right in the support chat %s "
                "(can_manage_topics=%s) — creating per-client topics will fail.",
                chat_id,
                getattr(member, "can_manage_topics", None),
            )
    except TelegramAPIError as exc:
        logger.warning(
            "Could not verify the support chat %s (wrong SUPPORT_CHAT_ID, or the "
            "bot is not in this chat?): %s",
            chat_id,
            exc,
        )


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
    await _check_support_chat(bot, me.id)

    from app.avito_webhook import start_web_server
    from app.config import get_settings
    from app.services.avito import close_avito, get_avito

    settings = get_settings()
    if get_avito().enabled:
        logger.info("Avito integration enabled")
    elif settings.avito_enabled:
        logger.warning(
            "AVITO_ENABLED=true, but AVITO_CLIENT_ID/AVITO_CLIENT_SECRET are not "
            "set — the Avito integration stays OFF."
        )
    webhook_runner = await start_web_server(bot, me.username or "")
    try:
        await dp.start_polling(bot)
    finally:
        if webhook_runner is not None:
            await webhook_runner.cleanup()
        await close_avito()
        await bot.session.close()
