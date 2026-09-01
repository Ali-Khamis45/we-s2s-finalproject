"""The contract between the backend and the frontend.

`docs/openapi.json` is committed, and `frontend/src/lib/types.ts` is generated
from it. That only helps if the committed copy stays current — otherwise the
frontend is generated from a schema the server no longer serves, which is the
same hand-transcription problem with extra steps.

This test fails when the two disagree, so a schema change must be committed
alongside the code that caused it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]
COMMITTED = REPO / "docs" / "openapi.json"


def _normalise(schema: dict) -> dict:
    """Ignore the version string, which moves with releases, not with the API."""
    copy = json.loads(json.dumps(schema, sort_keys=True))
    copy.setdefault("info", {})["version"] = "contract"
    return copy


@pytest.fixture(scope="module")
def committed() -> dict:
    if not COMMITTED.exists():
        pytest.fail(
            "docs/openapi.json is missing. Run:\n"
            "    python backend/scripts/dump_openapi.py"
        )
    return json.loads(COMMITTED.read_text(encoding="utf-8"))


class TestSchemaSnapshot:
    def test_committed_schema_matches_the_running_app(self, client: TestClient, committed):
        live = _normalise(client.get("/openapi.json").json())
        stored = _normalise(committed)

        if live != stored:
            live_paths = set(live.get("paths", {}))
            stored_paths = set(stored.get("paths", {}))
            added = sorted(live_paths - stored_paths)
            removed = sorted(stored_paths - live_paths)
            pytest.fail(
                "The committed OpenAPI schema is out of date.\n"
                f"  routes added since:   {added or 'none'}\n"
                f"  routes removed since: {removed or 'none'}\n"
                "Regenerate and commit it with the change that caused it:\n"
                "    python backend/scripts/dump_openapi.py"
            )

    def test_every_route_the_frontend_calls_exists(self, committed):
        """Guards the paths hand-written in lib/api.ts.

        The generated types cover shapes, not URLs — a renamed route would
        typecheck and 404 at runtime, which is the failure this catches.
        """
        paths = committed["paths"]
        for route in [
            "/api/status",
            "/api/chat",
            "/api/sessions",
            "/api/sessions/{session_id}",
            "/api/sessions/progress",
            "/api/auth/login",
            "/api/auth/register",
            "/api/auth/refresh",
            "/api/auth/logout",
            "/api/auth/me",
            "/api/auth/ws-ticket",
            "/api/auth/me/export",
        ]:
            assert route in paths, f"{route} is called by the frontend but not served"


class TestStatusShape:
    """`/api/status` returns a bare dict, so OpenAPI cannot describe it.

    `SystemStatus` in lib/types.ts is therefore hand-written, and this is what
    keeps it honest.
    """

    def test_status_keys_match_the_hand_written_type(self, client: TestClient):
        body = client.get("/api/status").json()
        expected = {
            "live_available": bool,
            "llm_reachable": bool,
            "stt_loaded": bool,
            "corpus_chunks": int,
            "analyzer": str,
            "prompt_version": str,
            "llm_variant": str,
        }
        assert set(body) == set(expected), (
            "GET /api/status changed shape. Update the SystemStatus interface in "
            "frontend/src/lib/types.ts in the same commit."
        )
        for key, kind in expected.items():
            assert isinstance(body[key], kind), f"{key} is not {kind.__name__}"


class TestErrorEnvelope:
    """Every failure the client can see wears the same shape."""

    @pytest.mark.parametrize(
        "method,url,expected",
        [
            ("GET", "/api/sessions/nope", 401),      # unauthenticated first
            ("GET", "/api/auth/me", 401),
            ("POST", "/api/chat", 401),
        ],
    )
    def test_errors_carry_code_and_message(
        self, client: TestClient, method: str, url: str, expected: int
    ):
        r = client.request(method, url, json={} if method == "POST" else None)
        assert r.status_code == expected
        error = r.json()["error"]
        assert isinstance(error["code"], str) and error["code"]
        assert isinstance(error["message"], str) and error["message"]

    def test_not_found_uses_the_same_envelope(self, authed_client: TestClient):
        r = authed_client.get("/api/sessions/definitely-not-real")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"
