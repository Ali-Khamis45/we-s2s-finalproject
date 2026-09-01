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
