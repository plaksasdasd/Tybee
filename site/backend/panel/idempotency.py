"""Exactly one committed action per actor, operation and client request key."""
from __future__ import annotations
import hashlib
import json
import re
from fastapi import HTTPException, Request
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import IdempotentRequest

async def claim(session: AsyncSession, request: Request, actor: int, action: str, payload: dict):
    raw = request.headers.get("Idempotency-Key")
    if raw is None:
        return None, None  # backwards compatibility; panel always supplies one
    if not re.fullmatch(r"[a-zA-Z0-9-]{16,64}", raw):
        raise HTTPException(422, "Некорректный Idempotency-Key")
    key = hashlib.sha256(f"{actor}:{action}:{raw}".encode()).hexdigest()
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode()).hexdigest()
    dialect = session.get_bind().dialect.name
    if dialect not in ("postgresql", "sqlite"):
        raise RuntimeError("Unsupported database for idempotency")
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    result = await session.execute(insert(IdempotentRequest).values(
        key=key, payload_hash=fingerprint
    ).on_conflict_do_nothing(index_elements=[IdempotentRequest.key]))
    old = await session.get(IdempotentRequest, key)
    if result.rowcount == 1:
        return old, None
    if old is None or old.result_id is None:
        raise HTTPException(409, "Запрос ещё выполняется; повторите позже")
    if old.payload_hash != fingerprint:
        raise HTTPException(409, "Этот ключ уже использован для другого запроса")
    return None, old.result_id
