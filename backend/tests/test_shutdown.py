"""Shutdown must not raise.

`dispose_db()` runs from the lifespan's shutdown half. By the time it runs the
event loop may already be closing — under Starlette's TestClient the anyio
portal tears its loop down first, and aiosqlite then cannot close its
connections. Disposal is best-effort cleanup of a process that is ending, so
raising there turns a clean exit into a failure: it made the whole backend
suite exit non-zero even though every test had passed.

Only the closed-loop case is tolerated. A genuine disposal fault still
propagates, because that one is worth knowing about.
"""

from __future__ import annotations

import asyncio

import pytest

from app.db import session as db_session


class FakeEngine:
    """Stands in for the AsyncEngine, whose `dispose` is read-only."""

    def __init__(self, error: Exception):
        self._error = error

    async def dispose(self) -> None:
        raise self._error


def test_a_closed_loop_does_not_break_shutdown(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        db_session, "engine", FakeEngine(RuntimeError("Event loop is closed"))
    )

    # Must not raise: the process is going away regardless.
    asyncio.run(db_session.dispose_db())


def test_a_real_disposal_fault_still_surfaces(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db_session, "engine", FakeEngine(RuntimeError("disk I/O error")))

    with pytest.raises(RuntimeError, match="disk I/O error"):
        asyncio.run(db_session.dispose_db())
