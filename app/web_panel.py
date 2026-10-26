from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any

from aiohttp import ContentTypeError, web
from aiogram.types import User as TelegramUser

from app import repositories as repo
from app.config import get_settings
from app.db.base import AVITO_ID_BASE, session_factory
from app.db.models import Order, SupportMessage, SupportOperator, User
from app.services import support

logger = logging.getLogger(__name__)

_STATIC = Path(__file__).with_name("web_static")
_COOKIE = "tybee_support_session"
_SESSION_TTL = 12 * 60 * 60
_AUTH_TTL = 10 * 60


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _session_key() -> bytes:
    return hmac.new(
        get_settings().bot_token.encode(), b"tybee-support-web-session-v1", hashlib.sha256
    ).digest()


def _sign_session(user_id: int, csrf: str) -> str:
    payload = _b64(
        json.dumps(
            {"sub": user_id, "exp": int(time.time()) + _SESSION_TTL, "csrf": csrf},
            separators=(",", ":"),
        ).encode()
    )
    return f"{payload}.{_b64(hmac.new(_session_key(), payload.encode(), hashlib.sha256).digest())}"


def _read_session(request: web.Request) -> dict[str, Any] | None:
    value = request.cookies.get(_COOKIE, "")
    try:
        payload, signature = value.split(".", 1)
        expected = _b64(hmac.new(_session_key(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(_unb64(payload))
        if not isinstance(data.get("sub"), int) or data["sub"] <= 0:
            return None
        if not isinstance(data.get("exp"), int) or data["exp"] < int(time.time()):
            return None
        if not isinstance(data.get("csrf"), str) or not data["csrf"]:
            return None
        return data
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _secure_cookie() -> bool:
    return get_settings().avito_webhook_url.startswith("https://")


def _json_error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


@web.middleware
async def context_middleware(request: web.Request, handler) -> web.StreamResponse:
    if not request.path.startswith("/api/") and request.path != "/auth/telegram":
        return await handler(request)
    async with session_factory() as session:
        request["db"] = session
        try:
            response = await handler(request)
            await session.commit()
            return response
        except support.SupportError as exc:
            await session.rollback()
            return _json_error(str(exc), exc.status)
        except Exception:
            await session.rollback()
            if request.path.startswith("/api/"):
                logger.exception("Web panel request failed: %s %s", request.method, request.path)
                return _json_error("Внутренняя ошибка сервера", 500)
            raise


@web.middleware
async def auth_middleware(request: web.Request, handler) -> web.StreamResponse:
    if not request.path.startswith("/api/") or request.path == "/api/public-config":
        return await handler(request)
    data = _read_session(request)
    staff = await support.get_staff(request["db"], data["sub"]) if data else None
    if data is None or staff is None:
        return _json_error("Требуется вход через Telegram", 401)
    request["staff"] = staff
    request["csrf"] = data["csrf"]
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(supplied, data["csrf"]):
            return _json_error("Недействительный CSRF-токен", 403)
    return await handler(request)


@web.middleware
async def security_middleware(request: web.Request, handler) -> web.StreamResponse:
    response = await handler(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.path.startswith("/api/") or request.path in {"/", "/login"}:
        response.headers["Cache-Control"] = "no-store"
    if request.path == "/" or request.path == "/login":
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://telegram.org https://oauth.telegram.org; "
            "frame-src https://telegram.org https://oauth.telegram.org; connect-src 'self'; "
            "img-src 'self' data: https://telegram.org https://oauth.telegram.org; "
            "style-src 'self'; base-uri 'none'"
        )
    return response


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ContentTypeError) as exc:
        raise support.SupportError("Некорректный JSON", 422) from exc
    if not isinstance(data, dict):
        raise support.SupportError("Ожидался JSON-объект", 422)
    return data


def _path_id(request: web.Request, name: str = "id") -> int:
    try:
        return int(request.match_info[name])
    except (KeyError, ValueError) as exc:
        raise support.SupportError("Некорректный идентификатор", 422) from exc


def _admin(request: web.Request) -> support.Staff:
    staff: support.Staff = request["staff"]
    if not staff.is_admin:
        raise support.SupportError("Доступно только администратору", 403)
    return staff


def _operator_json(operator: SupportOperator) -> dict[str, Any]:
    return {
        "telegram_id": operator.telegram_id,
        "display_name": operator.display_name,
        "is_active": operator.is_active,
    }


def _message_json(message: SupportMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "direction": message.direction,
        "body": message.body,
        "operator_name": message.operator_name,
        "channel": message.channel,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def _order_json(order: Order) -> dict[str, Any]:
    return {
        "id": order.id,
        "type": order.type,
        "status": order.status,
        "title": order.title,
        "description": order.description,
        "price": order.price,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
    }


def _client_json(user: User, latest: SupportMessage | None = None) -> dict[str, Any]:
    return {
        "id": user.id,
        "full_name": user.full_name,
        "username": user.username,
        "source": user.source,
        "is_banned": user.is_banned,
        "last_message": latest.body if latest else "",
        "last_activity": latest.created_at.isoformat() if latest and latest.created_at else None,
        "last_message_id": latest.id if latest else 0,
    }


async def index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(_STATIC / "index.html")


async def static_file(request: web.Request) -> web.StreamResponse:
    name = request.match_info["name"]
    if name not in {"app.js", "styles.css"}:
        raise web.HTTPNotFound
    return web.FileResponse(_STATIC / name)


async def public_config(request: web.Request) -> web.Response:
    return web.json_response({"bot_username": request.app["bot_username"]})


async def telegram_auth(request: web.Request) -> web.StreamResponse:
    values = dict(request.query)
    received_hash = values.pop("hash", "")
    if not received_hash or not values.get("id") or not values.get("auth_date"):
        return web.Response(text="Некорректный ответ Telegram", status=403)
    check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hashlib.sha256(get_settings().bot_token.encode()).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    try:
        auth_date = int(values["auth_date"])
        telegram_id = int(values["id"])
    except ValueError:
        return web.Response(text="Некорректный ответ Telegram", status=403)
    if (
        not hmac.compare_digest(received_hash, expected)
        or abs(int(time.time()) - auth_date) > _AUTH_TTL
        or not 0 < telegram_id < AVITO_ID_BASE
    ):
        return web.Response(text="Вход не подтверждён или ссылка устарела", status=403)
    existing = await repo.get_user(request["db"], telegram_id)
    operator = await repo.get_support_operator(request["db"], telegram_id)
    allowed = (
        telegram_id in get_settings().admin_ids
        or (existing is not None and repo.is_admin(existing))
        or (operator is not None and operator.is_active)
    )
    if not allowed:
        return web.Response(
            text="Доступ не выдан. Администратор должен добавить ваш Telegram ID в операторы.",
            status=403,
        )
    tg_user = TelegramUser(
        id=telegram_id,
        is_bot=False,
        first_name=values.get("first_name", ""),
        last_name=values.get("last_name"),
        username=values.get("username"),
    )
    user = await repo.get_or_create_user(request["db"], tg_user)
    user.is_admin = telegram_id in get_settings().admin_ids
    staff = await support.get_staff(request["db"], telegram_id)
    if staff is None:
        raise support.SupportError("Доступ не выдан", 403)
    csrf = secrets.token_urlsafe(32)
    response = web.HTTPFound("/")
    response.set_cookie(
        _COOKIE,
        _sign_session(telegram_id, csrf),
        max_age=_SESSION_TTL,
        httponly=True,
        secure=_secure_cookie(),
        samesite="Lax",
        path="/",
    )
    return response


async def session_info(request: web.Request) -> web.Response:
    staff: support.Staff = request["staff"]
    return web.json_response(
        {
            "user": {"id": staff.id, "name": staff.name, "is_admin": staff.is_admin},
            "csrf": request["csrf"],
            "bot_username": request.app["bot_username"],
        }
    )


async def logout(request: web.Request) -> web.Response:
    response = web.json_response({"ok": True})
    response.del_cookie(_COOKIE, path="/")
    return response


async def clients(request: web.Request) -> web.Response:
    query = request.query.get("q", "")[:100]
    try:
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError as exc:
        raise support.SupportError("Некорректное смещение", 422) from exc
    users, latest, total = await repo.list_support_clients(request["db"], query, offset)
    return web.json_response(
        {
            "clients": [_client_json(user, latest.get(user.id)) for user in users],
            "total": total,
        }
    )


async def client_detail(request: web.Request) -> web.Response:
    client_id = _path_id(request)
    client = await repo.get_user(request["db"], client_id)
    if client is None:
        raise support.SupportError("Клиент не найден", 404)
    try:
        after = max(0, int(request.query.get("after", "0")))
    except ValueError as exc:
        raise support.SupportError("Некорректный ID сообщения", 422) from exc
    messages = await repo.list_support_messages(request["db"], client_id, after)
    orders = await repo.list_orders(request["db"], client_id)
    latest = messages[-1] if messages else None
    return web.json_response(
        {
            "client": _client_json(client, latest),
            "orders": [_order_json(order) for order in orders],
            "messages": [_message_json(message) for message in messages],
        }
    )


async def send_message(request: web.Request) -> web.Response:
    data = await _body(request)
    if not isinstance(data.get("text"), str):
        raise support.SupportError("Введите сообщение", 422)
    await support.send_reply(
        request.app["bot"],
        request["db"],
        request["staff"],
        _path_id(request),
        data["text"],
        channel="web",
    )
    return web.json_response({"ok": True})


async def create_order(request: web.Request) -> web.Response:
    data = await _body(request)
    if not isinstance(data.get("title"), str) or not isinstance(data.get("description", ""), str):
        raise support.SupportError("Некорректные данные заказа", 422)
    order = await support.create_support_order(
        request.app["bot"],
        request["db"],
        request["staff"],
        _path_id(request),
        data["title"],
        data.get("description", ""),
        data.get("price"),
        channel="web",
    )
    return web.json_response({"order": _order_json(order)}, status=201)


async def payment(request: web.Request) -> web.Response:
    data = await _body(request)
    if not isinstance(data.get("approved"), bool):
        raise support.SupportError("Некорректное решение", 422)
    order = await support.decide_payment(
        request.app["bot"],
        request["db"],
        request["staff"],
        _path_id(request),
        data["approved"],
        channel="web",
    )
    return web.json_response({"order": _order_json(order)})


async def order_status(request: web.Request) -> web.Response:
    from app.db.models import OrderStatus

    data = await _body(request)
    try:
        status = OrderStatus(data.get("status"))
    except ValueError as exc:
        raise support.SupportError("Неизвестный статус", 422) from exc
    order = await support.change_order_status(
        request.app["bot"],
        request["db"],
        request["staff"],
        _path_id(request),
        status,
        channel="web",
    )
    return web.json_response({"order": _order_json(order)})


async def ban(request: web.Request) -> web.Response:
    data = await _body(request)
    if not isinstance(data.get("banned"), bool):
        raise support.SupportError("Некорректное состояние блокировки", 422)
    client = await support.set_client_banned(
        request["db"],
        request["staff"],
        _path_id(request),
        data["banned"],
        channel="web",
    )
    return web.json_response({"client": _client_json(client)})


def _operator_fields(data: dict[str, Any], *, creating: bool) -> tuple[str, bool]:
    name = data.get("display_name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise support.SupportError("Имя оператора обязательно и должно быть не длиннее 80 символов", 422)
    active = data.get("is_active", True if creating else None)
    if not isinstance(active, bool):
        raise support.SupportError("Некорректный статус оператора", 422)
    return name.strip(), active


async def operators(request: web.Request) -> web.Response:
    _admin(request)
    rows = await repo.list_support_operators(request["db"])
    return web.json_response({"operators": [_operator_json(row) for row in rows]})


async def add_operator(request: web.Request) -> web.Response:
    _admin(request)
    data = await _body(request)
    telegram_id = data.get("telegram_id")
    if not isinstance(telegram_id, int) or isinstance(telegram_id, bool) or not 0 < telegram_id < AVITO_ID_BASE:
        raise support.SupportError("Укажите корректный Telegram ID", 422)
    name, active = _operator_fields(data, creating=True)
    operator = await repo.save_support_operator(request["db"], telegram_id, name, active)
    return web.json_response({"operator": _operator_json(operator)}, status=201)


async def update_operator(request: web.Request) -> web.Response:
    staff = _admin(request)
    telegram_id = _path_id(request)
    operator = await repo.get_support_operator(request["db"], telegram_id)
    if operator is None:
        raise support.SupportError("Оператор не найден", 404)
    data = await _body(request)
    if "display_name" in data:
        name = data["display_name"]
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise support.SupportError("Некорректное имя оператора", 422)
        operator.display_name = name.strip()
    if "is_active" in data:
        if not isinstance(data["is_active"], bool):
            raise support.SupportError("Некорректный статус оператора", 422)
        if telegram_id == staff.id and not data["is_active"]:
            raise support.SupportError("Нельзя отключить собственную учётную запись", 409)
        operator.is_active = data["is_active"]
    return web.json_response({"operator": _operator_json(operator)})


def setup_web_panel(app: web.Application, bot_username: str) -> None:
    app["bot_username"] = bot_username
    app.middlewares.append(security_middleware)
    app.middlewares.append(context_middleware)
    app.middlewares.append(auth_middleware)
    app.router.add_get("/", index)
    app.router.add_get("/login", index)
    app.router.add_get("/static/{name}", static_file)
    app.router.add_get("/auth/telegram", telegram_auth)
    app.router.add_get("/api/public-config", public_config)
    app.router.add_get("/api/session", session_info)
    app.router.add_post("/api/logout", logout)
    app.router.add_get("/api/clients", clients)
    app.router.add_get("/api/clients/{id}", client_detail)
    app.router.add_post("/api/clients/{id}/messages", send_message)
    app.router.add_post("/api/clients/{id}/orders", create_order)
    app.router.add_post("/api/orders/{id}/payment", payment)
    app.router.add_post("/api/orders/{id}/status", order_status)
    app.router.add_post("/api/clients/{id}/ban", ban)
    app.router.add_get("/api/operators", operators)
    app.router.add_post("/api/operators", add_operator)
    app.router.add_patch("/api/operators/{id}", update_operator)


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})
