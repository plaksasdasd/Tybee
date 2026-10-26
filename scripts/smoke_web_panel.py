"""Smoke test for the real support-panel HTTP routes and Telegram authentication.

Run against a disposable PostgreSQL database:
    BOT_TOKEN=123:dummy ADMIN_IDS=999 POSTGRES_HOST=localhost POSTGRES_DB=taibi_test \
        SUPPORT_CHAT_ID= python -m scripts.smoke_web_panel

For a local lightweight check without PostgreSQL, install aiosqlite and set
SMOKE_SQLITE=1. The normal mode uses the project's production schema initializer.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import time

from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select

from app.config import get_settings
from app.db.base import Base, engine, init_models, session_factory
from app.db.models import SupportMessage, SupportOperator, User


def telegram_auth(telegram_id: int, first_name: str) -> dict[str, str]:
    values = {
        "id": str(telegram_id),
        "first_name": first_name,
        "auth_date": str(int(time.time())),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    values["hash"] = hmac.new(
        hashlib.sha256(get_settings().bot_token.encode()).digest(),
        check.encode(),
        hashlib.sha256,
    ).hexdigest()
    return values


async def login(client: TestClient, telegram_id: int, name: str) -> str:
    response = await client.get(
        "/auth/telegram", params=telegram_auth(telegram_id, name), allow_redirects=False
    )
    assert response.status == 302, await response.text()
    response = await client.get("/api/session")
    assert response.status == 200, await response.text()
    return (await response.json())["csrf"]


async def main() -> None:
    sqlite = os.environ.get("SMOKE_SQLITE") == "1"
    if not sqlite and get_settings().postgres_db == "taibi":
        raise SystemExit("Refusing to run the web smoke test against the 'taibi' database")
    if sqlite:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sf = async_sessionmaker(test_engine, expire_on_commit=False)
    else:
        test_engine = engine
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await init_models()
        sf = session_factory

    import app.web_panel as web_panel
    import scripts.smoke_flow as flow
    from app.avito_webhook import create_web_application

    web_panel.session_factory = sf
    async with sf() as session:
        session.add(User(id=flow.CUSTOMER_ID, full_name="Web Client"))
        await session.commit()

    bot = flow.RecorderBot(token="123:dummy")
    app = create_web_application(bot, "testbot")
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        assert (await client.get("/")).status == 200
        assert (await client.get("/static/app.js")).status == 200
        assert (await client.get("/api/session")).status == 401
        bad_auth = telegram_auth(flow.ADMIN_ID, "Administrator")
        bad_auth["hash"] = "0" * 64
        assert (await client.get("/auth/telegram", params=bad_auth)).status == 403
        denied_auth = telegram_auth(777, "Unknown")
        assert (await client.get("/auth/telegram", params=denied_auth)).status == 403
        async with sf() as session:
            assert await session.get(User, 777) is None

        csrf = await login(client, flow.ADMIN_ID, "Administrator")
        async with sf() as session:
            admin = await session.get(User, flow.ADMIN_ID)
            assert admin is not None and admin.is_admin
        headers = {"X-CSRF-Token": csrf}
        response = await client.post(
            f"/api/clients/{flow.CUSTOMER_ID}/messages", json={"text": "no csrf"}
        )
        assert response.status == 403

        response = await client.post(
            f"/api/clients/{flow.CUSTOMER_ID}/messages",
            json={"text": "Здравствуйте"},
            headers=headers,
        )
        assert response.status == 200, await response.text()
        sent = flow.last("SendMessage")
        assert "Administrator" in sent["text"] and "Здравствуйте" in sent["text"]

        response = await client.get("/api/clients?q=Web")
        data = await response.json()
        assert data["total"] == 1 and data["clients"][0]["id"] == flow.CUSTOMER_ID

        response = await client.post(
            f"/api/clients/{flow.CUSTOMER_ID}/orders",
            json={"title": "Куртка", "description": "Чёрная", "price": 6900},
            headers=headers,
        )
        assert response.status == 201, await response.text()
        order = (await response.json())["order"]

        response = await client.post(
            f"/api/orders/{order['id']}/status", json={"status": "bought"}, headers=headers
        )
        assert response.status == 409
        response = await client.post(
            f"/api/orders/{order['id']}/payment", json={"approved": True}, headers=headers
        )
        assert response.status == 200, await response.text()
        response = await client.post(
            f"/api/orders/{order['id']}/payment", json={"approved": True}, headers=headers
        )
        assert response.status == 409
        response = await client.post(
            f"/api/orders/{order['id']}/status", json={"status": "bought"}, headers=headers
        )
        assert response.status == 200, await response.text()

        for banned in (True, False):
            response = await client.post(
                f"/api/clients/{flow.CUSTOMER_ID}/ban",
                json={"banned": banned},
                headers=headers,
            )
            assert response.status == 200, await response.text()
            assert (await response.json())["client"]["is_banned"] is banned

        response = await client.post(
            "/api/operators",
            json={"telegram_id": 555, "display_name": "Мария"},
            headers=headers,
        )
        assert response.status == 201, await response.text()
        response = await client.post(
            "/api/operators",
            json={"telegram_id": 999, "display_name": "Главный админ"},
            headers=headers,
        )
        assert response.status == 201, await response.text()
        response = await client.patch(
            "/api/operators/999", json={"is_active": False}, headers=headers
        )
        assert response.status == 409

        csrf = await login(client, 555, "Maria")
        operator_headers = {"X-CSRF-Token": csrf}
        assert (await client.get("/api/operators")).status == 403
        response = await client.post(
            f"/api/clients/{flow.CUSTOMER_ID}/messages",
            json={"text": "Ответ оператора"},
            headers=operator_headers,
        )
        assert response.status == 200, await response.text()
        assert "Мария" in flow.last("SendMessage")["text"]

        response = await client.get(f"/api/clients/{flow.CUSTOMER_ID}")
        detail = await response.json()
        assert detail["orders"][0]["status"] == "bought"
        assert any(
            message["direction"] == "outbound" and message["operator_name"] == "Мария"
            for message in detail["messages"]
        )
        async with sf() as session:
            messages = list(await session.scalars(select(SupportMessage)))
            assert len(messages) >= 6
            operator = await session.get(SupportOperator, 555)
            operator.is_active = False
            await session.commit()
        assert (await client.get("/api/clients")).status == 401
    finally:
        await client.close()
        await bot.session.close()
        if sqlite:
            await test_engine.dispose()
    print("ALL WEB PANEL ASSERTIONS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
