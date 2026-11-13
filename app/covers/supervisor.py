"""Keeps the set of running covers in sync with the ``covers`` table.

Every ``covers_reload_sec`` seconds the table is compared with the running
jobs: new/enabled covers are validated and started (one asyncio task with its
own Bot and Dispatcher, long polling), disabled/removed ones are stopped, a
changed token restarts the job. A failure of one cover never touches the others.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.types import BotCommand
from sqlalchemy import select, update

from app.config import get_settings
from app.covers import loader, registry
from app.db import base as db_base
from app.db.models import Cover

logger = logging.getLogger(__name__)

_BACKOFF_START = 5.0
_BACKOFF_MAX = 60.0
_STOP_TIMEOUT = 10.0
_HOT_RELOAD_PREFIX = "hot-reload: "


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _default_bot_factory(token: str) -> Bot:
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def _default_dispatcher_factory(cover_id: int) -> Dispatcher:
    from app.bot import create_dispatcher

    return create_dispatcher(cover_id)


async def _default_poll(dp: Dispatcher, bot: Bot) -> None:
    await dp.start_polling(bot, handle_signals=False)


async def set_commands(bot: Bot, cover_id: int) -> None:
    """Command menu: /start only (administration lives on the site since ТЗ-2)."""
    start_description = loader.cover_text(cover_id, "CMD_START_DESCRIPTION")
    await bot.set_my_commands([BotCommand(command="start", description=start_description)])


@dataclass
class _Job:
    cover_id: int
    slug: str
    token_fp: str
    bot: Bot
    task: asyncio.Task | None = None
    dp: Dispatcher | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    unauthorized: bool = False


class Supervisor:
    def __init__(
        self,
        *,
        bot_factory: Callable[[str], Bot] | None = None,
        dispatcher_factory: Callable[[int], Dispatcher] | None = None,
        poll: Callable[[Dispatcher, Bot], Awaitable[None]] | None = None,
        reload_sec: float | None = None,
        backoff_start: float = _BACKOFF_START,
        backoff_max: float = _BACKOFF_MAX,
        stop_timeout: float = _STOP_TIMEOUT,
        apply_commands: bool = True,
    ) -> None:
        self._bot_factory = bot_factory or _default_bot_factory
        self._dispatcher_factory = dispatcher_factory or _default_dispatcher_factory
        self._poll = poll or _default_poll
        self._reload_sec = reload_sec
        self._backoff_start = backoff_start
        self._backoff_max = backoff_max
        self._stop_timeout = stop_timeout
        self._apply_commands = apply_commands
        self._jobs: dict[int, _Job] = {}
        # cover_id -> token fingerprint that got "Unauthorized": not retried
        # until the token (or enabled flag) changes.
        self._blocked: dict[int, str] = {}
        self._logged_errors: dict[int, str] = {}
        self._stopping = asyncio.Event()

    # ------------------------------------------------------------------ public
    @property
    def running_ids(self) -> set[int]:
        return {cid for cid, job in self._jobs.items() if job.task and not job.task.done()}

    async def run(self) -> None:
        interval = self._reload_sec or get_settings().covers_reload_sec
        while not self._stopping.is_set():
            try:
                await self.sync_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Cover sync failed")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), interval)

    async def shutdown(self) -> None:
        self._stopping.set()
        await asyncio.gather(
            *(self._stop_job(job) for job in list(self._jobs.values())), return_exceptions=True
        )

    # -------------------------------------------------------------------- sync
    async def sync_once(self) -> None:
        async with db_base.session_factory() as session:
            covers = list(await session.scalars(select(Cover).order_by(Cover.id)))
        by_id = {c.id: c for c in covers}

        # Stop what is disabled or gone.
        for cover_id, job in list(self._jobs.items()):
            cover = by_id.get(cover_id)
            if cover is None or not cover.enabled:
                await self._stop_job(job)
                self._blocked.pop(cover_id, None)
                if cover is None:
                    loader.forget_cover(cover_id)
        for cover in covers:
            if not cover.enabled:
                self._blocked.pop(cover.id, None)
                self._logged_errors.pop(cover.id, None)

        token_owner: dict[str, int] = {}
        for cover in covers:
            if not cover.enabled:
                continue
            loader.set_cover_path(cover.id, cover.path)
            job = self._jobs.get(cover.id)
            if job is not None and job.task is not None and job.task.done():
                await self._reap(job)
                job = None
            try:
                await self._sync_cover(cover, job, token_owner)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("cover %s: sync failed", cover.slug)

        await self._flush_runtime_errors(by_id)

    async def _sync_cover(self, cover: Cover, job: _Job | None, token_owner: dict[str, int]) -> None:
        folder = loader.resolve_path(cover.path)
        errors = loader.validate_cover_folder(folder)
        token = ""
        if not errors:
            token = loader.cover_token(cover.id)
            owner = token_owner.setdefault(token, cover.id)
            if owner != cover.id:
                errors = [
                    f"токен бота {loader.mask_token(token)} уже используется другой обложкой"
                ]
        if errors:
            message = "; ".join(errors[:10])
            if job is not None:
                # Do not interrupt a working cover because of a half-edited file;
                # loader keeps serving the last valid config meanwhile.
                await self._record_error(cover, _HOT_RELOAD_PREFIX + message)
                return
            await self._record_error(cover, message)
            return

        fp = _fingerprint(token)
        if job is not None:
            if (cover.last_error or "").startswith(_HOT_RELOAD_PREFIX):
                self._logged_errors.pop(cover.id, None)
                await self._update_cover(cover.id, last_error=None)
            if job.token_fp != fp:
                logger.info("cover %s: token changed, restarting", cover.slug)
                await self._stop_job(job)
                self._blocked.pop(cover.id, None)
                job = None
            else:
                return
        if self._blocked.get(cover.id) == fp:
            return
        await self._start(cover, token, fp)

    async def _start(self, cover: Cover, token: str, fp: str) -> None:
        bot = self._bot_factory(token)
        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError:
            self._blocked[cover.id] = fp
            await self._record_error(
                cover, f"Unauthorized: неверный токен бота {loader.mask_token(token)}"
            )
            await bot.session.close()
            return
        except Exception as exc:  # network trouble etc. — retry on the next tick
            logger.warning("cover %s: getMe failed (%s), will retry", cover.slug, type(exc).__name__)
            await bot.session.close()
            return

        await self._update_cover(
            cover.id, bot_id=me.id, bot_username=me.username, last_error=None
        )
        self._logged_errors.pop(cover.id, None)
        if self._apply_commands:
            try:
                await set_commands(bot, cover.id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("cover %s: could not set commands: %s", cover.slug, exc)

        job = _Job(cover_id=cover.id, slug=cover.slug, token_fp=fp, bot=bot)
        self._jobs[cover.id] = job
        registry.register(cover.id, cover.slug, bot)
        job.task = asyncio.create_task(self._run_cover(job), name=f"cover:{cover.slug}")
        logger.info("cover %s started (@%s)", cover.slug, me.username)

    # ---------------------------------------------------------------- per cover
    async def _run_cover(self, job: _Job) -> None:
        registry.cover_slug_var.set(job.slug)
        delay = self._backoff_start
        while not job.stop_event.is_set():
            job.dp = self._dispatcher_factory(job.cover_id)
            started = time.monotonic()
            try:
                await self._poll(job.dp, job.bot)
                return
            except asyncio.CancelledError:
                raise
            except TelegramUnauthorizedError:
                job.unauthorized = True
                self._blocked[job.cover_id] = job.token_fp
                await self._update_cover(
                    job.cover_id, last_error="Unauthorized: токен бота отклонён Telegram"
                )
                logger.error("cover %s: Unauthorized, polling stopped", job.slug)
                return
            except Exception:
                logger.exception("cover %s: polling crashed, restart in %.0fs", job.slug, delay)
            if time.monotonic() - started > self._backoff_max:
                delay = self._backoff_start
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(job.stop_event.wait(), delay)
            delay = min(delay * 2, self._backoff_max)

    async def _stop_job(self, job: _Job) -> None:
        job.stop_event.set()
        if job.dp is not None:
            with contextlib.suppress(Exception):
                await job.dp.stop_polling()
        task = job.task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), self._stop_timeout)
            except asyncio.TimeoutError:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            except Exception:  # noqa: BLE001
                pass
        await self._reap(job)
        logger.info("cover %s stopped", job.slug)

    async def _reap(self, job: _Job) -> None:
        if self._jobs.get(job.cover_id) is job:
            del self._jobs[job.cover_id]
        registry.unregister(job.cover_id)
        with contextlib.suppress(Exception):
            await job.bot.session.close()

    # ------------------------------------------------------------------- state
    async def _update_cover(self, cover_id: int, **values) -> None:
        async with db_base.session_factory() as session:
            await session.execute(update(Cover).where(Cover.id == cover_id).values(**values))
            await session.commit()

    async def _record_error(self, cover: Cover, message: str) -> None:
        if self._logged_errors.get(cover.id) != message:
            self._logged_errors[cover.id] = message
            logger.error("cover %s not started: %s", cover.slug, message)
        if cover.last_error != message:
            await self._update_cover(cover.id, last_error=message[:4000])

    async def _flush_runtime_errors(self, by_id: dict[int, Cover]) -> None:
        """Store hot-reload rejections (bad edit of a running cover's config)."""
        for cover_id, message in loader.pop_runtime_errors().items():
            cover = by_id.get(cover_id)
            if cover is None:
                continue
            if message is not None:
                await self._update_cover(cover_id, last_error=(_HOT_RELOAD_PREFIX + message)[:4000])
            elif (cover.last_error or "").startswith(_HOT_RELOAD_PREFIX):
                await self._update_cover(cover_id, last_error=None)
