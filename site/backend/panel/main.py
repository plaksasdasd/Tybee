from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.covers.service import CoverError
from app.db import base as db_base
from panel import auth, login_bot, support_worker, support_events
from app.db.models import Staff
from urllib.parse import urlsplit
from panel.routes import avito, broadcasts, catalog, covers, finance, invites, staff, support, login_settings, logistics, payroll, leaderboard

logger = logging.getLogger(__name__)
DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db_base.init_models()
    async with db_base.session_factory() as session:
        await auth.ensure_bootstrap_admins(session)
    bot_task = asyncio.create_task(login_bot.run_login_bot())
    support_task = asyncio.create_task(support_worker.run_support_worker())
    yield
    bot_task.cancel()
    support_task.cancel()
    await asyncio.gather(bot_task, support_task, return_exceptions=True)
    await db_base.engine.dispose()


def _topic(path: str) -> str | None:
    """Which browser caches a changed path invalidates."""
    if path.endswith("/read"):
        # Marking a thread read only moves unread counters; the message
        # history is unchanged, so it must not be re-downloaded.
        return "support-unread"
    if path.startswith("/api/support") or path == "/api/me/shift":
        return "support"
    if path.startswith(("/api/finance", "/api/payroll", "/api/leaderboard", "/api/me/payouts")):
        return "finance"
    if path.startswith(("/api/staff", "/api/me")):
        return "staff"
    return None


def create_app() -> FastAPI:
    settings = get_settings()
    secret = settings.web_session_secret
    if not secret:
        # Sessions then die on restart; fine for local runs, loud in logs.
        secret = secrets.token_hex(32)
        logger.warning("WEB_SESSION_SECRET is not set: using a random one (sessions reset on restart)")
    app = FastAPI(title="Tybee panel", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie="tybee_session",
        max_age=7 * 24 * 3600,
        same_site="strict",
        https_only=settings.web_cookie_secure,
    )
    # JSON answers and the JS/CSS bundle travel over slow links; compress them once here.
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.exception_handler(CoverError)
    async def cover_error_handler(_: Request, exc: CoverError):
        return JSONResponse({"detail": str(exc), "errors": exc.errors}, status_code=exc.status)

    @app.middleware("http")
    async def panel_changed(request: Request, call_next):
        """Tell open panels which cache to drop — this replaces most polling."""
        response = await call_next(request)
        path = request.url.path
        if (
            request.method in ("POST", "PATCH", "PUT", "DELETE")
            and response.status_code < 400
            and path != "/api/me/presence"  # every tab pings it; broadcasting would cause a refetch storm
        ):
            topic = _topic(path)
            if topic:
                support_events.publish(topic)
        return response

    @app.websocket("/api/support/events")
    async def support_events_socket(ws: WebSocket):
        parsed = urlsplit(ws.headers.get("origin", ""))
        if parsed.scheme not in ("http", "https") or parsed.netloc != ws.headers.get("host"):
            await ws.close(code=1008)
            return
        async def authorized():
            async with db_base.session_factory() as session:
                staff = await auth.session_staff(session, ws.session)
                return bool(staff and staff.is_active)
        if not await authorized():
            await ws.close(code=1008)
            return
        queue = support_events.subscribe()
        await ws.accept()
        try:
            while True:
                try:
                    topic = await asyncio.wait_for(queue.get(), timeout=25)
                except asyncio.TimeoutError:
                    # Idle heartbeat: keeps proxies from dropping the socket and
                    # re-checks the session of a long-lived connection.
                    topic = None
                if not await authorized():
                    await ws.close(code=1008)
                    return
                await ws.send_json({"type": "changed", "topic": topic or "ping"})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            support_events.unsubscribe(queue)

    app.include_router(auth.router)
    app.include_router(login_settings.router)
    app.include_router(covers.router)
    app.include_router(invites.router)
    app.include_router(staff.router)
    app.include_router(support.router)
    app.include_router(finance.router)
    app.include_router(payroll.router)
    app.include_router(leaderboard.router)
    app.include_router(logistics.router)
    app.include_router(avito.router)
    app.include_router(catalog.router)
    app.include_router(broadcasts.router)

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    if DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            candidate = (DIST / path).resolve()
            if path and candidate.is_file() and DIST in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
