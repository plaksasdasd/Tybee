from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_settings = get_settings()
engine = create_async_engine(
    _settings.database_url, pool_pre_ping=True, pool_size=20, max_overflow=30
)
session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Lightweight, idempotent column additions for databases created before these
# columns existed (the project ships no migration tool; create_all only adds
# whole tables, never new columns).
_COLUMN_PATCHES = (
    "ALTER TABLE orders ALTER COLUMN id TYPE BIGINT",
    "ALTER TABLE orders ALTER COLUMN id DROP DEFAULT",
    "ALTER TABLE users ALTER COLUMN current_order_id TYPE BIGINT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'telegram'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS avito_user_id BIGINT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS avito_chat_id VARCHAR(64)",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS description TEXT",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS photo_file_id VARCHAR(256)",
    "CREATE INDEX IF NOT EXISTS ix_users_username_lower ON users (lower(username))",
    "CREATE INDEX IF NOT EXISTS ix_users_avito_chat_id ON users (avito_chat_id)",
    # An older bug stored empty file_ids — they make Telegram reject sends
    # with MEDIA_EMPTY; wipe them so banners re-upload from disk.
    "DELETE FROM media_cache WHERE file_id = ''",
    # Multi-cover support (tables themselves come from create_all).
    "ALTER TABLE covers ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS cover_id INTEGER REFERENCES covers(id)",
    "CREATE INDEX IF NOT EXISTS ix_orders_cover_id ON orders (cover_id)",
    "ALTER TABLE support_outbox ADD COLUMN IF NOT EXISTS cover_id INTEGER",
    "ALTER TABLE support_inbox ADD COLUMN IF NOT EXISTS cover_id INTEGER",
    "ALTER TABLE robokassa_payments ADD COLUMN IF NOT EXISTS cover_id INTEGER",
    "ALTER TABLE staff ADD COLUMN IF NOT EXISTS position VARCHAR(128)",
    "ALTER TABLE staff ADD COLUMN IF NOT EXISTS created_by BIGINT",
    "ALTER TABLE staff ADD COLUMN IF NOT EXISTS on_shift BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE staff ADD COLUMN IF NOT EXISTS last_assigned_at TIMESTAMPTZ",
    # Stage 4 (finance). Tables come from create_all.
    "ALTER TABLE staff ADD COLUMN IF NOT EXISTS payout_requisites TEXT",
    "ALTER TABLE staff ADD COLUMN IF NOT EXISTS base_salary_kop INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS paid_amount INTEGER",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS paid_staff_id BIGINT",
    "CREATE INDEX IF NOT EXISTS ix_orders_paid_at ON orders (paid_at)",
    # One-off, approximate backfill for orders paid before paid_at existed (idempotent).
    "UPDATE orders SET paid_at = updated_at, paid_amount = price "
    "WHERE paid_at IS NULL AND status IN ('approved','accepted','bought','shipping_to_ru',"
    "'customs','shipping_in_ru','awaiting_pickup','received')",
    # ТЗ-2 block A: support attachments (the table comes from create_all).
    "ALTER TABLE support_outbox ADD COLUMN IF NOT EXISTS attachment_id BIGINT",
    "ALTER TABLE support_attachments ADD COLUMN IF NOT EXISTS ephemeral BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE support_attachments ADD COLUMN IF NOT EXISTS remote_provider VARCHAR(24)",
    "ALTER TABLE support_attachments ADD COLUMN IF NOT EXISTS remote_id TEXT",
    "ALTER TABLE support_attachments ADD COLUMN IF NOT EXISTS remote_cover_id INTEGER",
    "ALTER TABLE support_attachments ADD COLUMN IF NOT EXISTS remote_profile_id INTEGER",
    # ТЗ-2 block B: refunds and /ping.
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS refunded_at TIMESTAMPTZ",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS refund_amount INTEGER",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS last_ping_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS ix_orders_refunded_at ON orders (refunded_at)",
    # ТЗ-2 block C: Avito profiles (avito_profiles / avito_chats come from create_all).
    "ALTER TABLE support_inbox ADD COLUMN IF NOT EXISTS avito_chat_ref BIGINT REFERENCES avito_chats(id)",
    "ALTER TABLE support_outbox ADD COLUMN IF NOT EXISTS avito_chat_ref BIGINT REFERENCES avito_chats(id)",
    "ALTER TABLE support_threads ADD COLUMN IF NOT EXISTS last_avito_chat_ref BIGINT REFERENCES avito_chats(id)",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS avito_profile_id INTEGER REFERENCES avito_profiles(id)",
    # Support workspace: additive, repeatable upgrades; no history is removed.
    "ALTER TABLE support_threads ADD COLUMN IF NOT EXISTS needs_reply BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE support_threads ADD COLUMN IF NOT EXISTS waiting_since TIMESTAMPTZ",
    "ALTER TABLE support_threads ADD COLUMN IF NOT EXISTS read_through_id BIGINT",
    "ALTER TABLE support_threads ADD COLUMN IF NOT EXISTS handoff_note TEXT",
    "ALTER TABLE support_threads ADD COLUMN IF NOT EXISTS next_step TEXT",
    "ALTER TABLE support_threads ADD COLUMN IF NOT EXISTS due_at TIMESTAMPTZ",
    "ALTER TABLE support_thread_messages ADD COLUMN IF NOT EXISTS request_key VARCHAR(100)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_support_message_request_key ON support_thread_messages(request_key)",
    "ALTER TABLE support_outbox ADD COLUMN IF NOT EXISTS attachment_sent BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE support_outbox ADD COLUMN IF NOT EXISTS body_sent BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE support_outbox ADD COLUMN IF NOT EXISTS delivery_uncertain BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE support_outbox ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS ix_support_waiting ON support_threads(needs_reply, waiting_since, id)",
    # Recover open unanswered work once; read_through_id marks migrated records.
    "UPDATE support_threads SET needs_reply = TRUE, waiting_since = COALESCE(last_client_message_at, created_at) WHERE read_through_id IS NULL AND status = 'open' AND (unread > 0 OR EXISTS (SELECT 1 FROM support_thread_messages c WHERE c.thread_id = support_threads.id AND c.direction = 'client' AND NOT EXISTS (SELECT 1 FROM support_thread_messages r JOIN support_outbox o ON o.id = r.outbox_id WHERE r.thread_id = c.thread_id AND r.direction = 'staff' AND r.id > c.id AND o.processed_at IS NOT NULL AND o.last_error IS NULL)))",
    "UPDATE support_threads SET read_through_id = 0 WHERE read_through_id IS NULL",
    # Shift presence (staff_shifts comes from create_all).
    "ALTER TABLE staff ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ",
    "ALTER TABLE staff ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ",
    # Close older duplicate open shifts before enforcing the one-open-shift invariant.
    "UPDATE staff_shifts SET ended_at = CURRENT_TIMESTAMP, end_reason = 'deactivated' "
    "WHERE ended_at IS NULL AND id NOT IN (SELECT MAX(id) FROM staff_shifts WHERE ended_at IS NULL GROUP BY staff_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_staff_open_shift ON staff_shifts(staff_id) WHERE ended_at IS NULL",
)

AVITO_ID_BASE = 10 ** 12


async def init_models() -> None:
    # Import models so they are registered on Base.metadata before create_all.
    from app.db import models  # noqa: F401

    for attempt in range(10):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                for statement in _COLUMN_PATCHES:
                    await conn.execute(text(statement))
            await migrate_to_covers()
            return
        except Exception:
            logger.warning("DB not ready (attempt %d/10), retrying in 2s...", attempt + 1)
            await asyncio.sleep(2)
    raise RuntimeError("Could not connect to database after 10 attempts")


async def migrate_to_covers(factory=None) -> None:
    """Idempotent data migration to the multi-cover schema.

    Creates the ``default`` cover and moves the single-bot state of Telegram
    clients, orders, outbox and the banner cache onto it. Safe to run on every
    start: nothing is duplicated or overwritten when it is already done.
    """
    from sqlalchemy import select, update

    from app.db.models import (
        Cover,
        CoverMediaCache,
        MediaCache,
        Order,
        SupportOutbox,
        User,
        UserCover,
    )

    factory = factory or session_factory
    settings = get_settings()
    async with factory() as session:
        default = await session.scalar(select(Cover).where(Cover.slug == "default"))
        if default is None:
            default = Cover(
                slug="default", path=f"{settings.covers_root.rstrip('/')}/default", enabled=True
            )
            session.add(default)
            await session.flush()
        cover_id = default.id

        # 2. Telegram clients without a per-cover state get one on "default".
        missing = (
            select(
                User.id,
                User.state,
                User.screen_message_id,
                User.current_order_id,
            )
            .where(
                User.source == "telegram",
                ~select(UserCover.user_id)
                .where(UserCover.user_id == User.id, UserCover.cover_id == cover_id)
                .exists(),
            )
        )
        rows = (await session.execute(missing)).all()
        for user_id, state, screen_id, order_id in rows:
            session.add(
                UserCover(
                    user_id=user_id,
                    cover_id=cover_id,
                    state=state,
                    screen_message_id=screen_id,
                    current_order_id=order_id,
                )
            )

        # 3. Orders without a cover belong to the default one.
        await session.execute(
            update(Order).where(Order.cover_id.is_(None)).values(cover_id=cover_id)
        )
        # Not-yet-delivered outbox events of the single-bot era.
        await session.execute(
            update(SupportOutbox)
            .where(SupportOutbox.cover_id.is_(None), SupportOutbox.processed_at.is_(None))
            .values(cover_id=cover_id)
        )

        # 5. Banner file_id cache -> default cover (empty fingerprint = re-upload once).
        have = set(
            await session.scalars(
                select(CoverMediaCache.key).where(CoverMediaCache.cover_id == cover_id)
            )
        )
        for old in await session.scalars(select(MediaCache)):
            if old.key not in have and old.file_id:
                session.add(
                    CoverMediaCache(
                        cover_id=cover_id, key=old.key, file_id=old.file_id, fingerprint=""
                    )
                )
        await session.commit()


async def migrate_avito_profiles(factory=None) -> bool:
    """ТЗ-2 C.2, idempotent: env Avito keys -> profile ``default`` (+ chats of existing Avito clients).

    Runs only while there are no profiles at all. The webhook hash is taken from
    AVITO_WEBHOOK_SECRET so the existing subscription URL keeps working. Returns True
    when the profile was created.
    """
    from sqlalchemy import func, select

    from app.db.models import AvitoChat, AvitoProfile, User
    from app.services.avito_secrets import AvitoKeyError, encrypt_secret, webhook_hash

    settings = get_settings()
    client_id = settings.avito_client_id.strip()
    client_secret = settings.avito_client_secret.strip()
    if not (client_id and client_secret):
        return False
    factory = factory or session_factory
    async with factory() as session:
        if await session.scalar(select(func.count()).select_from(AvitoProfile)):
            return False
        try:
            secret_enc = encrypt_secret(client_secret)
        except AvitoKeyError as exc:
            logger.error("Avito profile 'default' not created from env: %s", exc)
            return False
        hook = settings.avito_webhook_secret.strip()
        profile = AvitoProfile(
            slug="default",
            title="Avito",
            client_id=client_id,
            client_secret_enc=secret_enc,
            webhook_secret_hash=webhook_hash(hook) if hook else None,
            enabled=bool(settings.avito_enabled),
        )
        session.add(profile)
        await session.flush()
        users = await session.scalars(
            select(User).where(User.source == "avito", User.avito_chat_id.is_not(None))
        )
        for user in users:
            session.add(AvitoChat(profile_id=profile.id, chat_id=user.avito_chat_id, user_id=user.id))
        await session.commit()
    logger.info("Avito profile 'default' created from AVITO_CLIENT_ID/AVITO_CLIENT_SECRET")
    return True
