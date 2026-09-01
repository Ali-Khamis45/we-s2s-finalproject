"""Test fixtures.

Every test runs against a throwaway SQLite file and never touches a model. The
point is to prove the plumbing — routing, persistence, prompt assembly,
degradation — holds independently of whether weights are downloaded, because
that is the state a fresh clone is in.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Must be set before app.core.config is imported anywhere.
_TMP = Path(tempfile.mkdtemp(prefix="scc-test-"))
os.environ["SCC_DATA_DIR"] = str(_TMP)
os.environ["SCC_DATABASE_URL"] = f"sqlite+aiosqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["SCC_CHROMA_DIR"] = str(_TMP / "chroma")
os.environ["SCC_CORPUS_DIR"] = str(_TMP / "corpus")
os.environ["SCC_MOSHI_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    """A client with the app's lifespan run, so the schema exists."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def llm_offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the LLM client at an address that will refuse the connection.

    Degradation tests must not assume nothing happens to be listening on the
    real port — anyone running llama-server locally (as the verification
    scripts do) would otherwise see these fail for the wrong reason. Port 9 is
    the discard service and is reliably closed.

    The httpx client binds its base URL at construction, so it is reset either
    side to force a rebuild.
    """
    from app.core.config import settings
    from app.services.llm import llm_service

    monkeypatch.setattr(settings, "llm_base_url", "http://127.0.0.1:9/v1")
    llm_service._client = None
    yield
    llm_service._client = None
