"""End-to-end flow smoke test.

Drives the real Dispatcher/handlers against the real PostgreSQL database, but
replaces the Telegram Bot API transport with a recorder so we can assert on the
exact API calls the bot makes (and prove navigation *edits* messages instead of
deleting them).

Run:
    BOT_TOKEN=... ADMIN_IDS=999 POSTGRES_HOST=localhost python -m scripts.smoke_flow
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.methods import (
    AnswerCallbackQuery,
    CopyMessage,
    EditMessageMedia,
    EditMessageReplyMarkup,
    GetMe,
    SendMessage,
    SendPhoto,
    SetMyCommands,
)
from aiogram.methods.base import TelegramMethod
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    MessageId,
    PhotoSize,
    Update,
)
from aiogram.types import User as TgUser

from app.bot import create_dispatcher
from app.db.base import engine, init_models
from app.db.seed import seed

CUSTOMER_ID = 1001
ADMIN_ID = 999

calls: list[tuple[str, dict]] = []
_msg_seq = 1000


def _next_id() -> int:
    global _msg_seq
    _msg_seq += 1
    return _msg_seq


def _fake_message(chat_id: int, *, text: str | None = None, photo: bool = False) -> Message:
    return Message(
        message_id=_next_id(),
        date=datetime.now(timezone.utc),
        chat=Chat(id=chat_id, type="private"),
        text=text,
        photo=[PhotoSize(file_id="FILEID", file_unique_id="U", width=100, height=100)]
        if photo
        else None,
    )


class RecorderBot(Bot):
    async def __call__(self, method: TelegramMethod, request_timeout=None):  # type: ignore[override]
        name = type(method).__name__
        data = method.model_dump(exclude_none=True)
        calls.append((name, data))

        if isinstance(method, GetMe):
            return TgUser(id=42, is_bot=True, first_name="Тайби", username="Taibytestbot")
        if isinstance(method, SetMyCommands):
            return True
        if isinstance(method, SendPhoto):
            return _fake_message(int(data["chat_id"]), photo=True)
        if isinstance(method, EditMessageMedia):
            return _fake_message(int(data["chat_id"]), photo=True)
        if isinstance(method, SendMessage):
            return _fake_message(int(data["chat_id"]), text=data.get("text"))
        if isinstance(method, CopyMessage):
            return MessageId(message_id=_next_id())
        if isinstance(method, (AnswerCallbackQuery, EditMessageReplyMarkup)):
            return True
        return True


def msg_update(user_id: int, text: str, reply_to: int | None = None) -> Update:
    m = Message(
        message_id=_next_id(),
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=TgUser(id=user_id, is_bot=False, first_name="Tester"),
        text=text,
        reply_to_message=_fake_message(user_id, text="ctx") if reply_to is None else
        Message(
            message_id=reply_to,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type="private"),
            text="relayed",
        )
        if reply_to
        else None,
    )
    return Update(update_id=_next_id(), message=m)


def cb_update(user_id: int, data: str, screen_id: int) -> Update:
    screen = _fake_message(user_id, photo=True)
    object.__setattr__(screen, "message_id", screen_id)
    cb = CallbackQuery(
        id=str(_next_id()),
        from_user=TgUser(id=user_id, is_bot=False, first_name="Tester"),
        chat_instance="ci",
        message=screen,
        data=data,
    )
    return Update(update_id=_next_id(), callback_query=cb)


def find(name: str) -> list[dict]:
    return [d for n, d in calls if n == name]


def last(name: str) -> dict:
    return [d for n, d in calls if n == name][-1]


async def main() -> None:
    from app.config import get_settings

    # Safety: this test drops all tables. Never let it run against the main DB.
    if get_settings().postgres_db == "taibi":
        raise SystemExit(
            "Refusing to run smoke test against the 'taibi' database. "
            "Set POSTGRES_DB=taibi_test (it uses fake file_ids that would break the live bot)."
        )

    # Clean slate for deterministic assertions.
    async with engine.begin() as conn:
        from app.db.base import Base

        await conn.run_sync(Base.metadata.drop_all)
    await init_models()
    await seed()

    bot = RecorderBot(token="123:dummy")
    dp = create_dispatcher()

    def screen_caption() -> str:
        return last("EditMessageMedia")["media"]["caption"]

    # 1) /start -> first screen is SENT (no existing message yet)
    await dp.feed_update(bot, msg_update(CUSTOMER_ID, "/start"))
    assert find("SendPhoto"), "Expected initial welcome photo"
    screen_id = find("SendPhoto")[0] and 5001
    # the bot stored screen_message_id from our fake SendPhoto return
    from app.db.base import session_factory
    from app.db.models import User

    async with session_factory() as s:
        cust = await s.get(User, CUSTOMER_ID)
        screen_id = cust.screen_message_id
    print("welcome sent; screen_id=", screen_id)

    # 2) open order -> "уже есть артикул?" (must be an EDIT, not a new message)
    await dp.feed_update(bot, cb_update(CUSTOMER_ID, "order:start", screen_id))
    assert "уже есть артикул" in screen_caption().lower() or "оформление заказа" in screen_caption().lower()
    print("order:start edited ->", screen_caption()[:40])

    # 3) has article
    await dp.feed_update(bot, cb_update(CUSTOMER_ID, "order:has_article", screen_id))
    assert "артикул" in screen_caption().lower()

    # 4) enter a valid catalog article -> found -> approved + pay button
    await dp.feed_update(bot, msg_update(CUSTOMER_ID, "482915"))
    cap = screen_caption()
    assert "одобрили" in cap.lower() and "14900" in cap, cap
    print("article found ->", cap.replace(chr(10), " ")[:60])

    async with session_factory() as s:
        cust = await s.get(User, CUSTOMER_ID)
        order_id = cust.current_order_id
    print("order created #", order_id)

    # 5) proceed to payment
    await dp.feed_update(bot, cb_update(CUSTOMER_ID, f"pay:proceed:{order_id}", screen_id))
    assert "оплата по реквизитам" in screen_caption().lower()

    # 6) "Я оплатил" -> review screen + admin notification with confirm/reject kb
    calls.clear()
    await dp.feed_update(bot, cb_update(CUSTOMER_ID, f"pay:done:{order_id}", screen_id))
    assert "чуть-чуть" in screen_caption().lower()
    admin_msgs = find("SendMessage")
    assert any(str(ADMIN_ID) == str(m["chat_id"]) for m in admin_msgs), "admin not notified"
    assert any("reply_markup" in m for m in admin_msgs), "no confirm/reject kb"
    print("paid -> review screen + admin notified")

    # 7) admin confirms payment -> customer screen edited to APPROVED, NO delete
    calls.clear()
    await dp.feed_update(bot, cb_update(ADMIN_ID, f"adm:confirm:{order_id}", screen_id))
    edits_to_customer = [d for d in find("EditMessageMedia") if int(d["chat_id"]) == CUSTOMER_ID]
    assert edits_to_customer, "customer screen not edited on approval"
    assert "одобрили" in edits_to_customer[-1]["media"]["caption"].lower()
    assert not find("DeleteMessage"), "bot deleted a message (should only edit!)"
    print("admin confirmed -> customer screen edited to APPROVED")

    # 8) open dialog
    await dp.feed_update(bot, cb_update(CUSTOMER_ID, f"dialog:open:{order_id}", screen_id))

    # 9) customer message in dialog -> relayed to admin (header + copy)
    calls.clear()
    await dp.feed_update(bot, msg_update(CUSTOMER_ID, "Когда будет доставка?"))
    assert any(int(m["chat_id"]) == ADMIN_ID for m in find("SendMessage")), "dialog not relayed"
    assert find("CopyMessage"), "message content not copied to support"
    print("dialog relay -> admin received header + copy")

    # 10) admin replies in support chat -> relayed back to customer
    async with session_factory() as s:
        from app.db.models import RelayMap
        from sqlalchemy import select

        rid = (await s.scalars(select(RelayMap.support_message_id))).all()[-1]
    calls.clear()
    await dp.feed_update(bot, msg_update(ADMIN_ID, "Доставим за 7-10 дней", reply_to=rid))
    back = [m for m in find("SendMessage") if int(m["chat_id"]) == CUSTOMER_ID]
    assert back, "admin reply not relayed to customer"
    assert "Команда" in back[-1]["text"]
    print("admin reply -> relayed to customer")

    # 11) reject path on a fresh order
    await dp.feed_update(bot, msg_update(CUSTOMER_ID, "/start"))
    await dp.feed_update(bot, cb_update(CUSTOMER_ID, "order:start", screen_id))
    await dp.feed_update(bot, cb_update(CUSTOMER_ID, "order:has_article", screen_id))
    await dp.feed_update(bot, msg_update(CUSTOMER_ID, "000000"))  # not in catalog
    assert "нет в каталоге" in screen_caption().lower()
    print("unknown article -> 'нет в каталоге'")

    await bot.session.close()
    print("\nALL FLOW ASSERTIONS PASSED ✅")


if __name__ == "__main__":
    asyncio.run(main())
