"""Telegram Login Widget verification, sessions and role checks."""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.login_bot_config import token as login_bot_token
from app.db.models import PanelSession, Staff, StaffRole
from app.support.shift import LOGOUT, end_shift
from panel.audit import write_audit
from panel.deps import get_session
from panel.permissions import ROLE_LABELS, Perm, perms_of

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

MAX_AUTH_AGE_SEC = 24 * 3600
_bot_username_cache: str = ""
_bot_username_token: str = ""


def verify_telegram_login(data: dict[str, Any], bot_token: str, now: float | None = None) -> bool:
    """Check the HMAC-SHA256 signature of Login Widget data and its freshness."""
    if not bot_token:
        return False
    fields = {k: str(v) for k, v in data.items() if v is not None}
    received = fields.pop("hash", "")
    if not received or "id" not in fields or "auth_date" not in fields:
        return False
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return False
    try:
        age = (now if now is not None else time.time()) - int(fields["auth_date"])
    except ValueError:
        return False
    return -60 <= age <= MAX_AUTH_AGE_SEC


def _session_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_session(
    request: Request, session: AsyncSession, staff: Staff, *, dev: bool = False
) -> None:
    old = request.session.get("session_token")
    if isinstance(old, str):
        await session.execute(delete(PanelSession).where(PanelSession.token_hash == _session_hash(old)))
    await session.execute(delete(PanelSession).where(PanelSession.expires_at <= datetime.now(timezone.utc)))
    token = secrets.token_urlsafe(32)
    session.add(PanelSession(token_hash=_session_hash(token), staff_id=staff.telegram_id,
                             expires_at=datetime.now(timezone.utc) + timedelta(days=7)))
    await session.commit()
    request.session.clear()
    request.session["session_token"] = token
    if dev:
        request.session["dev_login"] = True


async def session_staff(session: AsyncSession, cookie: dict) -> Staff | None:
    token = cookie.get("session_token")
    if cookie.get("dev_login") and not get_settings().web_dev_login:
        if isinstance(token, str) and len(token) <= 128:
            await session.execute(delete(PanelSession).where(PanelSession.token_hash == _session_hash(token)))
            await session.commit()
        return None
    if not isinstance(token, str) or len(token) > 128:
        return None
    row = await session.get(PanelSession, _session_hash(token))
    if row is None:
        return None
    expiry = row.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    staff = await session.get(Staff, row.staff_id)
    if expiry <= datetime.now(timezone.utc) or staff is None or not staff.is_active:
        await session.delete(row)
        await session.commit()
        return None
    return staff


async def current_staff(request: Request, session: AsyncSession = Depends(get_session)) -> Staff:
    staff = await session_staff(session, request.session)
    if staff is None:
        request.session.clear()
        raise HTTPException(401, "Требуется вход или доступ отключён")
    return staff


def require_role(*roles: StaffRole):
    allowed = {r.value for r in roles}

    async def dependency(staff: Staff = Depends(current_staff)) -> Staff:
        if staff.role != StaffRole.ADMIN.value and staff.role not in allowed:
            raise HTTPException(403, "Недостаточно прав")
        return staff

    return dependency


def require_perm(*perms: Perm):
    needed = set(perms)

    async def dependency(staff: Staff = Depends(current_staff)) -> Staff:
        if not needed <= perms_of(staff):
            raise HTTPException(403, "Недостаточно прав")
        return staff

    return dependency


async def csrf_guard(request: Request) -> None:
    if request.method not in ("GET", "HEAD", "OPTIONS") and not request.headers.get("x-requested-with"):
        raise HTTPException(403, "Отсутствует заголовок X-Requested-With")


def staff_json(staff: Staff) -> dict[str, Any]:
    return {
        "telegram_id": staff.telegram_id,
        "display_name": staff.display_name,
        "username": staff.username,
        "photo_url": staff.photo_url,
        "role": staff.role,
        "role_label": _label(staff.role),
        "position": staff.position,
        "on_shift": bool(staff.on_shift),
        "permissions": sorted(p.value for p in perms_of(staff)),
    }


def _label(role: str) -> str:
    try:
        return ROLE_LABELS[StaffRole(role)]
    except (ValueError, KeyError):
        return role


async def ensure_bootstrap_admins(session: AsyncSession) -> None:
    for tg_id in get_settings().web_admin_ids:
        staff = await session.get(Staff, tg_id)
        if staff is None:
            session.add(Staff(telegram_id=tg_id, display_name=str(tg_id), role=StaffRole.ADMIN.value))
        else:
            staff.role = StaffRole.ADMIN.value
            staff.is_active = True
    await session.commit()


async def _login_bot_username() -> str:
    global _bot_username_cache, _bot_username_token
    settings = get_settings()
    token = login_bot_token()
    if token != _bot_username_token:
        _bot_username_cache = ""
        _bot_username_token = token
    if settings.web_login_bot_username and token == settings.web_login_bot_token:
        return settings.web_login_bot_username.lstrip("@")
    if _bot_username_cache or not token:
        return _bot_username_cache
    from aiogram import Bot

    bot = Bot(token)
    try:
        _bot_username_cache = (await bot.get_me()).username or ""
    except Exception:  # noqa: BLE001
        logger.warning("getMe for the login bot failed")
    finally:
        await bot.session.close()
    return _bot_username_cache


@router.get("/config")
async def auth_config() -> dict[str, Any]:
    settings = get_settings()
    return {"bot_username": await _login_bot_username(), "dev_login": settings.web_dev_login}


@router.get("/me")
async def me(staff: Staff = Depends(current_staff)) -> dict[str, Any]:
    return staff_json(staff)


@router.post("/telegram", dependencies=[Depends(csrf_guard)])
async def telegram_login(
    payload: dict[str, Any], request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    if not verify_telegram_login(payload, login_bot_token()):
        raise HTTPException(401, "Подпись Telegram неверна или устарела")
    staff = await session.get(Staff, int(payload["id"]))
    if staff is None or not staff.is_active:
        raise HTTPException(403, "Вы не числитесь в сотрудниках")
    name = " ".join(p for p in (payload.get("first_name"), payload.get("last_name")) if p)
    staff.display_name = name or staff.display_name
    staff.username = payload.get("username") or staff.username
    staff.photo_url = payload.get("photo_url") or staff.photo_url
    await write_audit(session, staff, "auth.login", str(staff.telegram_id), {"method": "telegram"})
    await session.commit()
    await issue_session(request, session, staff)
    return staff_json(staff)


# ------------------------------------------------- login through the login bot
# Works without /setdomain (localhost, LAN, any host): the site shows a t.me deep link,
# the user presses "Confirm" in the bot, the browser that started the attempt polls and
# gets the session. Attempts live in memory: the login bot runs in this same process.
BOT_LOGIN_TTL_SEC = 300
BOT_LOGIN_PREFIX = "login_"


@dataclass
class BotLoginAttempt:
    created: float
    staff_id: int | None = None
    profile: dict[str, Any] = field(default_factory=dict)


_bot_logins: dict[str, BotLoginAttempt] = {}


def _gc_bot_logins(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    for token in [t for t, a in _bot_logins.items() if now - a.created > BOT_LOGIN_TTL_SEC]:
        _bot_logins.pop(token, None)


def bot_login_pending(token: str) -> bool:
    _gc_bot_logins()
    attempt = _bot_logins.get(token)
    return attempt is not None and attempt.staff_id is None


async def confirm_bot_login(
    session: AsyncSession, token: str, tg_id: int, name: str | None, username: str | None
) -> Staff:
    """Called by the login bot when the user confirms. Raises HTTPException with a user-facing detail."""
    if not bot_login_pending(token):
        raise HTTPException(410, "Ссылка для входа устарела. Обновите страницу входа на сайте.")
    staff = await session.get(Staff, tg_id)
    if staff is None or not staff.is_active:
        raise HTTPException(403, "Вы не числитесь в сотрудниках — обратитесь к руководителю за приглашением.")
    _bot_logins[token].staff_id = tg_id
    _bot_logins[token].profile = {"name": name, "username": username}
    return staff


@router.post("/bot-login", dependencies=[Depends(csrf_guard)])
async def bot_login_start(request: Request) -> dict[str, Any]:
    username = await _login_bot_username()
    if not username:
        raise HTTPException(503, "Бот входа не настроен (WEB_LOGIN_BOT_TOKEN)")
    _gc_bot_logins()
    old = request.session.get("bot_login")
    if old:
        _bot_logins.pop(old, None)
    if len(_bot_logins) >= 4096:
        raise HTTPException(503, "Слишком много попыток входа, попробуйте позже")
    token = secrets.token_urlsafe(18)
    _bot_logins[token] = BotLoginAttempt(created=time.time())
    request.session["bot_login"] = token
    return {
        "url": f"https://t.me/{username}?start={BOT_LOGIN_PREFIX}{token}",
        "bot_username": username,
        "expires_in": BOT_LOGIN_TTL_SEC,
    }


@router.get("/bot-login")
async def bot_login_poll(request: Request, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    token = request.session.get("bot_login")
    _gc_bot_logins()
    attempt = _bot_logins.get(token) if token else None
    if attempt is None:
        request.session.pop("bot_login", None)
        return {"status": "expired"}
    if attempt.staff_id is None:
        return {"status": "pending"}
    _bot_logins.pop(token, None)
    staff = await session.get(Staff, attempt.staff_id)
    if staff is None or not staff.is_active:
        request.session.clear()
        raise HTTPException(403, "Вы не числитесь в сотрудниках")
    if attempt.profile.get("name"):
        staff.display_name = attempt.profile["name"]
    staff.username = attempt.profile.get("username") or staff.username
    await write_audit(session, staff, "auth.login", str(staff.telegram_id), {"method": "telegram_bot"})
    await session.commit()
    await issue_session(request, session, staff)
    return {"status": "ok", "me": staff_json(staff)}


class DevLogin(BaseModel):
    telegram_id: int | None = None


# Roles in the order the matrix lists them (admin first, client managers last): the
# picker then reads like the org chart instead of the alphabet. ``ROLE_LABELS`` keeps
# that order in its keys, while the StaffRole enum is declared alphabetically.
_ROLE_ORDER = {role.value: index for index, role in enumerate(ROLE_LABELS)}


@router.get("/dev-staff")
async def dev_staff(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Everyone a dev-mode visitor may sign in as — the whole team, one entry per role."""
    if not get_settings().web_dev_login:
        raise HTTPException(404, "Not found")
    staff = await session.scalars(select(Staff).where(Staff.is_active.is_(True)))
    items = [
        {"telegram_id": s.telegram_id, "display_name": s.display_name, "role": s.role,
         "role_label": _label(s.role)}
        for s in staff
    ]
    items.sort(key=lambda item: (_ROLE_ORDER.get(item["role"], len(ROLE_LABELS)), item["display_name"].lower()))
    return {"items": items}


@router.post("/dev-login", dependencies=[Depends(csrf_guard)])
async def dev_login(
    body: DevLogin, request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.web_dev_login:
        raise HTTPException(404, "Not found")
    tg_id = body.telegram_id if body.telegram_id is not None else (settings.web_admin_ids[0] if settings.web_admin_ids else 0)
    staff = await session.get(Staff, tg_id)
    if staff is None and tg_id == 0:
        # No WEB_ADMIN_IDS configured: a throwaway local admin so dev mode just works.
        staff = Staff(telegram_id=0, display_name="Dev admin", role=StaffRole.ADMIN.value)
        session.add(staff)
        await session.commit()
    if staff is None:
        raise HTTPException(403, "В списке dev-входа нет такого сотрудника")
    if not staff.is_active:
        raise HTTPException(403, "Этот сотрудник отключён — dev-вход невозможен")
    await write_audit(session, staff, "auth.login", str(staff.telegram_id), {"method": "dev"})
    await session.commit()
    await issue_session(request, session, staff, dev=True)
    return staff_json(staff)


@router.post("/logout", dependencies=[Depends(csrf_guard)])
async def logout(request: Request, session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    staff = await session_staff(session, request.session)
    token = request.session.get("session_token")
    if isinstance(token, str):
        await session.execute(delete(PanelSession).where(PanelSession.token_hash == _session_hash(token)))
    request.session.clear()
    # Leaving the panel ends the shift: otherwise threads keep landing on someone who is gone.
    if staff is not None and await end_shift(session, staff, LOGOUT):
        await write_audit(session, staff, "support.shift", str(staff.telegram_id), {"on_shift": False, "reason": LOGOUT})
    await session.commit()
    return {"ok": True}


# ------------------------------------------------- one-time sign-in link from the bot
# After accepting an invite (or on /start) the login bot sends a button with a single-use
# link; opening it signs the employee in and lands them in the panel — no second login.
MAGIC_TTL_SEC = 600


@dataclass
class MagicLink:
    created: float
    staff_id: int


_magic_links: dict[str, MagicLink] = {}


def issue_magic_link(staff_id: int) -> str | None:
    """Absolute URL that signs ``staff_id`` in once; None when WEB_PUBLIC_URL is not set."""
    base = get_settings().web_public_url.rstrip("/")
    if not base:
        return None
    now = time.time()
    for token in [t for t, m in _magic_links.items() if now - m.created > MAGIC_TTL_SEC]:
        _magic_links.pop(token, None)
    token = secrets.token_urlsafe(24)
    _magic_links[token] = MagicLink(created=now, staff_id=staff_id)
    return f"{base}/api/auth/magic/{token}"


@router.get("/magic/{token}", include_in_schema=False)
async def magic_login(token: str, request: Request, session: AsyncSession = Depends(get_session)):
    link = _magic_links.pop(token, None)
    if link is None or time.time() - link.created > MAGIC_TTL_SEC:
        return RedirectResponse("/login?link=expired", status_code=303)
    staff = await session.get(Staff, link.staff_id)
    if staff is None or not staff.is_active:
        return RedirectResponse("/login?link=denied", status_code=303)
    await write_audit(session, staff, "auth.login", str(staff.telegram_id), {"method": "telegram_link"})
    await session.commit()
    await issue_session(request, session, staff)
    return RedirectResponse("/me", status_code=303)
