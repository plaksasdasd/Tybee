from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import texts
from app.db.models import User

router = Router(name="admin")


async def _resolve_command_target(
    session: AsyncSession, args: str | None
) -> tuple[User | None, str | None]:
    token = args.split()[0] if args else ""
    if not token:
        return None, None
    if token.startswith("@") or not token.lstrip("-").isdigit():
        target = await repo.get_user_by_username(session, token)
        if target is None:
            return None, texts.TARGET_USERNAME_NOT_FOUND.format(username=html.escape(token))
        return target, None
    return await repo.get_or_create_user_by_id(session, int(token)), None


@router.message(Command("ban"), F.chat.type == "private")
async def cmd_ban(
    message: Message, command: CommandObject, session: AsyncSession, user: User
) -> None:
    await _set_ban(message, command, session, user, True)


@router.message(Command("pardon"), F.chat.type == "private")
async def cmd_pardon(
    message: Message, command: CommandObject, session: AsyncSession, user: User
) -> None:
    await _set_ban(message, command, session, user, False)


async def _set_ban(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    banned: bool,
) -> None:
    if not repo.is_admin(user):
        await message.reply(texts.CMD_NOT_ALLOWED)
        return
    target, error = await _resolve_command_target(session, command.args)
    if error is not None:
        await message.reply(error, parse_mode="HTML")
        return
    if target is None:
        await message.reply(texts.BAN_USAGE if banned else texts.PARDON_USAGE, parse_mode="HTML")
        return
    if target.is_banned == banned:
        await message.reply(texts.ALREADY_BANNED if banned else texts.NOT_BANNED)
        return
    await repo.set_banned(session, target, banned)
    text = texts.BAN_DONE if banned else texts.PARDON_DONE
    await message.reply(text + f"\n{repo.display_name(target) or target.id}")
