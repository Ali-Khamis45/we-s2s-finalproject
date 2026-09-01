"""Error envelope.

Every failure the client can see comes back in one shape:

    {"error": {"code": "model_unavailable", "message": "...", "detail": {...},
               "request_id": "a1b2c3"}}

Messages are written for the person using the coach, not the developer reading
the traceback: say what went wrong and what happens next.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, get_request_id

log = get_logger(__name__)


class AppError(Exception):
    """Base for errors that map onto a client-visible response."""

    code = "internal_error"
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    message = "Something went wrong on our side."

    def __init__(
        self,
        message: str | None = None,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message
        self.detail = detail or {}

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail:
            body["detail"] = self.detail
        if rid := get_request_id():
            body["request_id"] = rid
        return {"error": body}


class NotFoundError(AppError):
    code = "not_found"
    status_code = status.HTTP_404_NOT_FOUND
    message = "That doesn't exist."


class ValidationError(AppError):
    code = "invalid_request"
    # Literal 422 rather than the constant: Starlette renamed
    # HTTP_422_UNPROCESSABLE_ENTITY to HTTP_422_UNPROCESSABLE_CONTENT, and the
    # old name warns on import in newer versions while the new one is absent in
    # older ones.
    status_code = 422
    message = "The request wasn't valid."


class ModelUnavailableError(AppError):
    """A model or its weights are not reachable.

    Raised rather than crashing so the orchestrator can degrade (A14): if Moshi
    is down the session continues on the cascade, and the user is told the coach
    is running in its slower mode rather than being shown a dead page.
    """

    code = "model_unavailable"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    message = "That model isn't loaded yet."


class DependencyMissingError(AppError):
    """An optional Python package isn't installed.

    The message names the package so the fix is obvious on a fresh clone.
    """

    code = "dependency_missing"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    message = "A required package isn't installed."

    def __init__(self, package: str, purpose: str) -> None:
        super().__init__(
            f"{purpose} needs the '{package}' package, which isn't installed. "
            f"Run: pip install -r backend/requirements.txt",
            detail={"package": package},
        )


class CorpusEmptyError(AppError):
    code = "corpus_empty"
    status_code = status.HTTP_409_CONFLICT
    message = (
        "The knowledge base is empty. Add documents to data/corpus and run "
        "POST /api/corpus/ingest."
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            log.exception("unhandled app error", extra={"code": exc.code})
        else:
            log.warning("app error", extra={"code": exc.code, "reason": exc.message})
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        err = ValidationError(detail={"fields": exc.errors()})
        return JSONResponse(status_code=err.status_code, content=err.to_payload())

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        err = AppError(str(exc.detail))
        err.code = "http_error"
        err.status_code = exc.status_code
        return JSONResponse(status_code=exc.status_code, content=err.to_payload())

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled exception", extra={"type": type(exc).__name__})
        err = AppError()
        return JSONResponse(status_code=err.status_code, content=err.to_payload())
