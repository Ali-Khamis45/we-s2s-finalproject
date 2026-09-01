"""Structured logging.

Latency is the project's headline measurement (M10, M12), so timing is a first
class logging concern here rather than something bolted on at benchmark time:
`stage()` emits one record per pipeline stage with its duration in milliseconds.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from app.core.config import settings

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_session_id: ContextVar[str | None] = ContextVar("session_id", default=None)

_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime", "taskName"}


def safe_extra(fields: dict[str, Any]) -> dict[str, Any]:
    """Rename fields that collide with LogRecord's own attributes.

    `logging` raises KeyError when `extra` carries a reserved name such as
    `msg`, `name`, `module`, or `args` — and because that happens *inside* the
    logging call, a collision turns a log line into an exception. In an error
    handler that is especially bad: the failure being reported gets replaced by
    a failure to report it. Colliding keys are prefixed rather than dropped, so
    the value still reaches the log.
    """
    if not fields:
        return {}
    return {(f"x_{k}" if k in _RESERVED else k): v for k, v in fields.items()}


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def set_request_id(value: str | None) -> None:
    _request_id.set(value)


def set_session_id(value: str | None) -> None:
    _session_id.set(value)


def get_request_id() -> str | None:
    return _request_id.get()


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get() or "-"
        record.session_id = _session_id.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "session_id": getattr(record, "session_id", "-"),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable, with any structured extras appended as key=value."""

    _BASE = "%(asctime)s %(levelname)-7s %(name)-28s [%(request_id)s] %(message)s"

    def __init__(self) -> None:
        super().__init__(self._BASE, datefmt="%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _RESERVED and k not in ("request_id", "session_id")
        }
        if extras:
            base += "  " + " ".join(f"{k}={v}" for k, v in extras.items())
        return base


def configure_logging() -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(settings.log_level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if settings.log_json else ConsoleFormatter())
    handler.addFilter(ContextFilter())
    root.addHandler(handler)

    # These are chatty at INFO and drown out our own pipeline timings.
    for noisy in ("httpx", "httpcore", "chromadb", "sentence_transformers", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


@contextmanager
def stage(logger: logging.Logger, name: str, **fields: Any) -> Iterator[dict[str, Any]]:
    """Time one pipeline stage and log its duration.

    The yielded dict is writable: put anything the caller learns mid-stage in it
    (token counts, chunk counts) and it lands on the same log record.

        with stage(log, "retrieval", k=4) as s:
            docs = retrieve(...)
            s["hits"] = len(docs)
    """
    extra: dict[str, Any] = dict(fields)
    started = time.perf_counter()
    try:
        yield extra
    except Exception:
        extra["ms"] = round((time.perf_counter() - started) * 1000, 1)
        logger.exception("stage.failed", extra=safe_extra({"stage": name, **extra}))
        raise
    else:
        extra["ms"] = round((time.perf_counter() - started) * 1000, 1)
        logger.info("stage.ok", extra=safe_extra({"stage": name, **extra}))
