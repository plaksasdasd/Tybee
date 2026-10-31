"""Cross-bot support integration smoke test using two temporary SQL databases."""
# ruff: noqa: E402
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "support_bot"))

from aiogram import Bot
from aiogram.methods import (
    AnswerCallbackQuery,
    CopyMessage,
    EditMessageMedia,
    EditMessageReplyMarkup,
    EditMessageText,
    GetMe,
    SendInvoice,
    SendMessage,
    SendPhoto,
    SetMyCommands,
)
from aiogram.methods.base import TelegramMethod
from aiogram.types import CallbackQuery, Chat, Message, MessageId, PhotoSize, Update
from aiogram.types import User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot import create_dispatcher as create_main_dispatcher
from app.db.base import Base as MainBase
from app.db.models import Order, SupportInbox, SupportOutbox, User
from support_bot.bot import create_dispatcher as create_support_dispatcher
from support_bot.db import Base as SupportBase
from support_bot.models import Staff, SupportRequest
from support_bot.source_db import SourceBase

CUSTOMER_ID = 1001
STAFF_ID = 555
OTHER_STAFF_ID = 666
ADMIN_ID = 999
AVITO_USER_ID = 1000000000501
_seq = 1000


def next_id() -> int:
    global _seq
    _seq += 1
    return _seq


def fake_message(chat_id: int, text: str | None = None, photo: bool = False) -> Message:
    return Message(
        message_id=next_id(),
        date=datetime.now(timezone.utc),
        chat=Chat(id=chat_id, type="private"),
        text=text,
        photo=[PhotoSize(file_id="FILE", file_unique_id="U", width=10, height=10)]
        if photo
        else None,
    )


def msg(user_id: int, text: str) -> Update:
    message = Message(
        message_id=next_id(),
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=TgUser(id=user_id, is_bot=False, first_name=f"User {user_id}"),
        text=text,
    )
    return Update(update_id=next_id(), message=message)


def cb(user_id: int, data: str) -> Update:
    callback = CallbackQuery(
        id=str(next_id()),
        from_user=TgUser(id=user_id, is_bot=False, first_name=f"User {user_id}"),
        chat_instance="test",
        message=fake_message(user_id),
        data=data,
    )
    return Update(update_id=next_id(), callback_query=callback)


class Recorder(Bot):
    def __init__(self, token: str):
        super().__init__(token=token)
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, method: TelegramMethod, request_timeout=None):
        name = type(method).__name__
        data = method.model_dump(exclude_none=True)
        self.calls.append((name, data))
        if isinstance(method, GetMe):
            return TgUser(id=42, is_bot=True, first_name="Test")
        if isinstance(method, (SetMyCommands, AnswerCallbackQuery, EditMessageReplyMarkup, EditMessageText)):
            return True
        if isinstance(method, SendPhoto):
            return fake_message(int(data["chat_id"]), photo=True)
        if isinstance(method, EditMessageMedia):
            return fake_message(int(data["chat_id"]), photo=True)
        if isinstance(method, (SendMessage, SendInvoice)):
            return fake_message(int(data["chat_id"]), data.get("text"))
        if isinstance(method, CopyMessage):
            return MessageId(message_id=next_id())
        return True

    def texts_to(self, chat_id: int) -> list[str]:
        return [
            data.get("text", "")
            for name, data in self.calls
            if name == "SendMessage" and int(data.get("chat_id", 0)) == chat_id
        ]


class FakeAvito:
    account_id = 1
    enabled = True

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))

    async def get_chat_user_name(self, chat_id: str) -> str:
        return "Avito Tester"


async def main() -> None:
    import app.avito_webhook as avito_webhook
    import app.middlewares as main_middleware
    import app.services.outbox as main_outbox
    import support_bot.middlewares as support_middleware
    import support_bot.source_db as source_db
    import support_bot.source_importer as importer
    import support_bot.source_repository as source_repository

    main_engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    support_engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with main_engine.begin() as conn:
        await conn.run_sync(MainBase.metadata.create_all)
        await conn.run_sync(SourceBase.metadata.create_all)
    async with support_engine.begin() as conn:
        await conn.run_sync(SupportBase.metadata.create_all)

    main_sf = async_sessionmaker(main_engine, class_=AsyncSession, expire_on_commit=False)
    support_sf = async_sessionmaker(support_engine, class_=AsyncSession, expire_on_commit=False)
    main_middleware.session_factory = main_sf
    main_outbox.session_factory = main_sf
    avito_webhook.session_factory = main_sf
    support_middleware.session_factory = support_sf
    importer.session_factory = support_sf
    importer.source_session_factory = main_sf
    source_db.source_session_factory = main_sf
    source_repository.source_session_factory = main_sf

    async with main_sf() as session:
        session.add(User(id=ADMIN_ID, full_name="Admin", is_admin=True))
        await session.commit()
    async with support_sf() as session:
        session.add_all(
            [
                Staff(telegram_id=STAFF_ID, display_name="Сотрудник #555"),
                Staff(telegram_id=OTHER_STAFF_ID, display_name="Сотрудник #666"),
            ]
        )
        await session.commit()

    main_bot = Recorder("123:main")
    support_bot = Recorder("123:support")
    main_dp = create_main_dispatcher()
    support_dp = create_support_dispatcher()

    # Main Telegram client -> inbox -> support request -> claim -> outbox.
    await main_dp.feed_update(main_bot, msg(CUSTOMER_ID, "/start"))
    await main_dp.feed_update(main_bot, cb(CUSTOMER_ID, "support:open"))
    await main_dp.feed_update(main_bot, msg(CUSTOMER_ID, "Нужна помощь с заказом"))
    async with main_sf() as session:
        event = (await session.scalars(select(SupportInbox))).one()
        assert event.body == "Нужна помощь с заказом" and event.processed_at is None
    assert await importer.process_next_source_event(support_bot)
    async with support_sf() as session:
        request = (await session.scalars(select(SupportRequest))).one()
        request_id = request.id
        assert request.source == "main" and request.channel == "telegram"
    notified = {int(data["chat_id"]) for name, data in support_bot.calls if name == "SendMessage" and "request:claim" in str(data)}
    assert {ADMIN_ID, STAFF_ID, OTHER_STAFF_ID} <= notified

    await support_dp.feed_update(support_bot, cb(STAFF_ID, f"request:claim:{request_id}"))
    await support_dp.feed_update(support_bot, cb(OTHER_STAFF_ID, f"request:claim:{request_id}"))
    async with support_sf() as session:
        request = await session.get(SupportRequest, request_id)
        assert request.assigned_to == STAFF_ID and request.assigned_name == "User 555"
    assert await main_outbox.process_next_outbox(main_bot)
    assert any("взял ваше обращение" in text for text in main_bot.texts_to(CUSTOMER_ID))

    await support_dp.feed_update(support_bot, msg(STAFF_ID, "Здравствуйте, чем помочь?"))
    assert await main_outbox.process_next_outbox(main_bot)
    assert any("Здравствуйте" in text for text in main_bot.texts_to(CUSTOMER_ID))
    print("Telegram dialog + assignment + reply passed")

    # Support-created invoice -> client payment -> support decision -> status.
    await support_dp.feed_update(support_bot, msg(STAFF_ID, "/order Куртка зимняя 6900"))
    async with main_sf() as session:
        order = (await session.scalars(select(Order))).one()
        order_id = order.id
        assert order.status == "awaiting_payment" and order.price == 6900
    assert await main_outbox.process_next_outbox(main_bot)
    invoice = main_bot.texts_to(CUSTOMER_ID)[-1]
    assert "6900" in invoice

    await main_dp.feed_update(main_bot, cb(CUSTOMER_ID, f"pay:done:{order_id}"))
    assert await importer.process_next_source_event(support_bot)
    await support_dp.feed_update(support_bot, cb(STAFF_ID, f"order:confirm:{order_id}"))
    async with main_sf() as session:
        assert (await session.get(Order, order_id)).status == "approved"
    assert await main_outbox.process_next_outbox(main_bot)
    approval_calls = [
        data
        for name, data in main_bot.calls
        if name == "SendMessage" and int(data.get("chat_id", 0)) == CUSTOMER_ID and "dialog:open" in str(data)
    ]
    assert approval_calls

    await support_dp.feed_update(support_bot, cb(STAFF_ID, f"order:status:{order_id}:bought"))
    async with main_sf() as session:
        assert (await session.get(Order, order_id)).status == "bought"
    assert await main_outbox.process_next_outbox(main_bot)
    assert any("Выкуплен" in text for text in main_bot.texts_to(CUSTOMER_ID))

    await support_dp.feed_update(support_bot, msg(STAFF_ID, "/ban"))
    async with main_sf() as session:
        assert (await session.get(User, CUSTOMER_ID)).is_banned
    await support_dp.feed_update(support_bot, msg(STAFF_ID, "/pardon"))
    async with main_sf() as session:
        assert not (await session.get(User, CUSTOMER_ID)).is_banned
    print("Invoice + payment + status + ban/pardon passed")

    # Avito uses the same inbox/outbox transport.
    fake_avito = FakeAvito()
    avito_webhook.get_avito = lambda: fake_avito
    main_outbox.get_avito = lambda: fake_avito
    await avito_webhook.process_avito_message(
        {
            "id": "avito-1",
            "chat_id": "chat-1",
            "author_id": 501,
            "content": {"type": "text", "text": "Вопрос из Avito"},
        }
    )
    assert await importer.process_next_source_event(support_bot)
    async with support_sf() as session:
        avito_request = await session.scalar(
            select(SupportRequest).where(SupportRequest.customer_id == AVITO_USER_ID)
        )
        assert avito_request is not None and avito_request.channel == "avito"
    await support_dp.feed_update(support_bot, cb(STAFF_ID, f"request:claim:{avito_request.id}"))
    await main_outbox.process_next_outbox(main_bot)
    await support_dp.feed_update(support_bot, msg(STAFF_ID, "Ответ в Avito"))
    await main_outbox.process_next_outbox(main_bot)
    assert any(chat == "chat-1" and "Ответ в Avito" in text for chat, text in fake_avito.sent)
    print("Avito dialog + reply passed")

    # Closing in the main bot is imported into the support DB.
    await main_dp.feed_update(main_bot, cb(CUSTOMER_ID, "dialog:close"))
    assert await importer.process_next_source_event(support_bot)
    async with support_sf() as session:
        assert (await session.get(SupportRequest, request_id)).status == "closed"
    async with main_sf() as session:
        remaining = await session.scalar(
            select(SupportOutbox).where(SupportOutbox.processed_at.is_(None))
        )
        assert remaining is None
    print("Dialog close passed")

    # Admin broadcast uses preview + confirmation and excludes Avito users.
    from app.handlers import broadcast as broadcast_handler

    await main_dp.feed_update(main_bot, cb(ADMIN_ID, "adm:broadcast"))
    await main_dp.feed_update(main_bot, msg(ADMIN_ID, "Сообщение всем клиентам"))
    await main_dp.feed_update(main_bot, cb(ADMIN_ID, "adm:broadcast:send"))
    task = broadcast_handler._broadcast_task
    assert task is not None
    await task
    copied_recipients = {
        int(data["chat_id"])
        for name, data in main_bot.calls
        if name == "CopyMessage"
    }
    assert CUSTOMER_ID in copied_recipients and ADMIN_ID in copied_recipients
    assert AVITO_USER_ID not in copied_recipients
    assert any("Рассылка завершена" in text for text in main_bot.texts_to(ADMIN_ID))
    print("Admin broadcast passed")

    await main_bot.session.close()
    await support_bot.session.close()
    await main_engine.dispose()
    await support_engine.dispose()
    print("\nALL INTEGRATION ASSERTIONS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
