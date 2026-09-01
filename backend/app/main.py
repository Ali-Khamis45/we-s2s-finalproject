"""Application entrypoint (A2).

Run with:  uvicorn app.main:app --reload   (from the backend/ directory)

Startup deliberately loads no models. On a fresh clone nothing has been
downloaded, and a server that refuses to boot without weights is a server
nobody can debug â€” `GET /api/status` reports what is actually available, and
each service loads on first use. Set SCC_EAGER_LOAD=1 to warm STT and TTS at
boot instead, which is what you want before a demo.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes import auth, chat, corpus, health, knowledge, live, sessions
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import (
    configure_logging,
    get_logger,
    new_request_id,
    set_request_id,
    set_session_id,
)
from app.db.session import dispose_db, init_db
from app.services.llm import llm_service

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    # Refuses to start on a missing or weak signing secret outside debug. A
    # committed default would let anyone mint a token for any account.
    settings.validate_runtime()
    settings.ensure_dirs()
    await init_db()

    log.info(
        "starting",
        extra={
            "version": __version__,
            "live_coach": settings.moshi_enabled,
            "llm": settings.llm_model,
            "variant": settings.llm_variant,
        },
    )

    if os.getenv("SCC_EAGER_LOAD", "").lower() in {"1", "true", "yes"}:
        from app.services.stt import stt_service
        from app.services.tts import tts_service

        log.info("eager loading models")
        await stt_service.warmup()
        await tts_service.warmup()

    try:
        yield
    finally:
        await llm_service.aclose()
        await dispose_db()
        log.info("stopped")


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description=(
        "A native speech-to-speech coach for building communication confidence. "
        "Accessibility tool, not a medical device â€” see docs/ETHICS.md."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Tag every request so its log lines can be followed end to end."""
    rid = request.headers.get("x-request-id") or new_request_id()
    set_request_id(rid)
    set_session_id(None)
    try:
        response = await call_next(request)
    except Exception:
        # Handlers registered below turn this into the error envelope; this only
        # guarantees the id reaches the client either way.
        log.exception("request failed", extra={"path": request.url.path})
        raise
    response.headers["x-request-id"] = rid
    return response


register_error_handlers(app)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(sessions.router)
app.include_router(corpus.router)
app.include_router(live.router)
app.include_router(knowledge.router)


@app.get("/", include_in_schema=False)
async def root() -> JSONResponse:
    return JSONResponse(
        {
            "name": settings.app_name,
            "version": __version__,
            "docs": "/docs",
            "status": "/api/status",
        }
    )

