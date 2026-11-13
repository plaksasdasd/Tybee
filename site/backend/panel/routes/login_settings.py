from aiogram import Bot
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app import login_bot_config
from app.db.models import Cover, Staff
from panel.audit import write_audit
from panel.auth import _login_bot_username, csrf_guard, require_perm
from panel.deps import get_session
from panel.permissions import Perm

router = APIRouter(prefix="/api/login-bot", dependencies=[Depends(csrf_guard)])
admin = require_perm(Perm.COVERS_MANAGE)


class TokenBody(BaseModel):
    token: str = Field(min_length=1, max_length=256)


@router.get("")
async def get_login_bot(_: Staff = Depends(admin)):
    return {"configured": bool(login_bot_config.token()), "username": await _login_bot_username()}


@router.put("")
async def set_login_bot(body: TokenBody, me: Staff = Depends(admin), session: AsyncSession = Depends(get_session)):
    token = body.token.strip()
    bot = None
    try:
        bot = Bot(token)
        info = await bot.get_me()
        if not info.is_bot or not info.username:
            raise ValueError("Not a bot")
    except Exception:
        raise HTTPException(422, "Не удалось проверить токен бота в Telegram") from None
    finally:
        if bot is not None:
            await bot.session.close()
    if await session.scalar(select(Cover.id).where(Cover.bot_id == info.id)) is not None:
        raise HTTPException(409, "Этот бот уже используется как обложка. Выберите отдельного бота входа.")
    login_bot_config.save(token)
    await write_audit(session, me, "login_bot.update", None, {"username": info.username})
    await session.commit()
    return {"configured": True, "username": info.username}
