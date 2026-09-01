"""The account boundary.

This file exists to fail. If someone adds a session-scoped route later and
forgets ownership, the parameterised test below is what should go red — which
is why the route list is written out rather than discovered, and why every one
of them is asserted to return 404 rather than 403.

A 403 would confirm the id is real and simply not yours, which is itself a
disclosure. "Absent" and "not yours" must be indistinguishable.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import make_account


@pytest.fixture(autouse=True)
def _clean_limiter():
    from app.services.auth import limiter

    limiter.clear()
    yield
    limiter.clear()


@pytest.fixture
def two_accounts(client: TestClient):
    """Two accounts, each owning one session with one turn of history."""
    alice = make_account(client)
    alice_session = client.post("/api/sessions", headers=alice["headers"]).json()["id"]

    bob = make_account(client)
    bob_session = client.post("/api/sessions", headers=bob["headers"]).json()["id"]

    return alice, alice_session, bob, bob_session


# Every route that takes a session id. Add to this list whenever one is added.
SESSION_SCOPED = [
    ("GET", "/api/sessions/{sid}"),
    ("GET", "/api/sessions/{sid}/metrics"),
    ("POST", "/api/sessions/{sid}/end"),
    ("DELETE", "/api/sessions/{sid}"),
]


class TestCrossAccount:
    @pytest.mark.parametrize("method,template", SESSION_SCOPED)
    def test_other_accounts_session_is_404_everywhere(
        self, client: TestClient, two_accounts, method: str, template: str
    ):
        alice, _, bob, bob_session = two_accounts
        url = template.format(sid=bob_session)

        r = client.request(method, url, headers=alice["headers"])
        assert r.status_code == 404, f"{method} {url} leaked with {r.status_code}"
        assert r.json()["error"]["code"] == "not_found"

    def test_rename_across_the_boundary_is_404(self, client: TestClient, two_accounts):
        alice, _, _, bob_session = two_accounts
        r = client.patch(
            f"/api/sessions/{bob_session}",
            headers=alice["headers"],
            json={"title": "not yours"},
        )
        assert r.status_code == 404

    def test_listing_only_returns_your_own(self, client: TestClient, two_accounts):
        alice, alice_session, bob, bob_session = two_accounts

        alice_ids = {s["id"] for s in client.get("/api/sessions", headers=alice["headers"]).json()}
        bob_ids = {s["id"] for s in client.get("/api/sessions", headers=bob["headers"]).json()}

        assert alice_session in alice_ids and bob_session not in alice_ids
        assert bob_session in bob_ids and alice_session not in bob_ids

    def test_progress_never_averages_a_strangers_speech(
        self, client: TestClient, two_accounts
    ):
        """An unscoped aggregate would silently mix two people's pacing."""
        alice, alice_session, bob, bob_session = two_accounts

        alice_points = client.get(
            "/api/sessions/progress", headers=alice["headers"]
        ).json()["points"]
        assert all(p["session_id"] != bob_session for p in alice_points)

    def test_chat_cannot_write_into_someone_elses_session(
        self, client: TestClient, two_accounts
    ):
        alice, _, _, bob_session = two_accounts
        r = client.post(
            "/api/chat",
            headers=alice["headers"],
            json={"message": "hello", "session_id": bob_session},
        )
        # 404 for the session, never a 200 that appends to Bob's thread.
        assert r.status_code == 404


class TestUnauthenticated:
    @pytest.mark.parametrize(
        "method,url",
        [
            ("GET", "/api/sessions"),
            ("POST", "/api/sessions"),
            ("GET", "/api/sessions/progress"),
            ("POST", "/api/chat"),
            ("GET", "/api/corpus"),
            ("GET", "/api/auth/me"),
        ],
    )
    def test_protected_routes_require_a_token(
        self, client: TestClient, method: str, url: str
    ):
        r = client.request(method, url, json={} if method == "POST" else None)
        assert r.status_code == 401

    @pytest.mark.parametrize("url", ["/health", "/api/status", "/api/schema/acoustic"])
    def test_public_routes_stay_public(self, client: TestClient, url: str):
        assert client.get(url).status_code == 200

    def test_a_forged_token_is_rejected(self, client: TestClient):
        r = client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.token"})
        assert r.status_code == 401
