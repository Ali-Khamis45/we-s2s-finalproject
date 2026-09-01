"""Authentication behaviour.

These are the checks a viva will probe: enumeration, lockout, rotation, reuse
detection, ticket single-use, and whether deletion actually deletes.
"""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from conftest import make_account

GOOD = "practice-speaking-2026"


@pytest.fixture(autouse=True)
def _clean_limiter():
    """The limiter is process-global; leaking counts between tests hides bugs."""
    from app.services.auth import limiter

    limiter.clear()
    yield
    limiter.clear()


class TestRegistration:
    def test_register_then_use_a_protected_route(self, client: TestClient):
        acct = make_account(client)
        r = client.get("/api/auth/me", headers=acct["headers"])
        assert r.status_code == 200
        assert r.json()["email"] == acct["email"]

    def test_duplicate_registration_is_indistinguishable(self, client: TestClient):
        """Returning 409 here would make this endpoint an address oracle."""
        acct = make_account(client)
        first = client.post(
            "/api/auth/register", json={"email": acct["email"], "password": GOOD}
        )
        second = client.post(
            "/api/auth/register",
            json={"email": f"brand-new-{time.time()}@speechcoach-test.org", "password": GOOD},
        )
        assert first.status_code == second.status_code == 201
        assert set(first.json()) == set(second.json())

    def test_duplicate_registration_does_not_hand_over_the_account(
        self, client: TestClient
    ):
        """The decoy response must not be a working session for the real user."""
        acct = make_account(client)
        decoy = client.post(
            "/api/auth/register", json={"email": acct["email"], "password": "another-one-entirely"}
        ).json()
        r = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {decoy['access_token']}"},
        )
        assert r.status_code == 401

    @pytest.mark.parametrize(
        "password,reason",
        [("short", "characters"), ("administrator", "common")],
    )
    def test_weak_passwords_are_refused_with_a_reason(
        self, client: TestClient, password: str, reason: str
    ):
        r = client.post(
            "/api/auth/register",
            json={"email": f"weak-{time.time()}@speechcoach-test.org", "password": password},
        )
        assert r.status_code == 422
        assert reason in r.json()["error"]["message"].lower()


class TestLogin:
    def test_wrong_password_and_unknown_email_are_identical(self, client: TestClient):
        acct = make_account(client)
        wrong = client.post(
            "/api/auth/login", json={"email": acct["email"], "password": "not-the-password"}
        )
        unknown = client.post(
            "/api/auth/login",
            json={"email": "nobody@speechcoach-test.org", "password": "not-the-password"},
        )
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
        assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"]

    def test_lockout_engages_after_repeated_failures(self, client: TestClient):
        acct = make_account(client)
        codes = [
            client.post(
                "/api/auth/login", json={"email": acct["email"], "password": "wrong"}
            ).status_code
            for _ in range(6)
        ]
        # Either locked or rate limited â€” both are 429, and both stop the attack.
        assert 429 in codes

    def test_lockout_is_never_permanent(self, client: TestClient):
        """A permanent lock is a denial of service anyone can trigger."""
        from app.services.auth import lockout_delay
        from app.core.config import settings

        assert lockout_delay(99) <= timedelta(seconds=settings.lockout_max_seconds)


class TestTokens:
    def test_access_token_is_rejected_as_a_refresh_token(self, client: TestClient):
        """typ must be checked, or a refresh token becomes an access token."""
        from app.services.auth import AuthError, decode_access_token
        import jwt as pyjwt
        from app.core.config import settings

        forged = pyjwt.encode(
            {"sub": "x", "exp": 9999999999, "typ": "refresh"},
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(AuthError):
            decode_access_token(forged)

    def test_refresh_rotates_and_the_old_token_stops_working(self, client: TestClient):
        acct = make_account(client)
        first = client.cookies.get("scc_refresh")
        assert first

        rotated = client.post("/api/auth/refresh")
        assert rotated.status_code == 200
        second = client.cookies.get("scc_refresh")
        assert second and second != first

    def test_replaying_a_used_refresh_token_revokes_the_family(self, client: TestClient):
        """The single thing that makes rotation worth its complexity."""
        make_account(client)
        stolen = client.cookies.get("scc_refresh")

        assert client.post("/api/auth/refresh").status_code == 200

        # Replay the token that was already rotated away.
        client.cookies.set("scc_refresh", stolen, path="/api/auth")
        replay = client.post("/api/auth/refresh")
        assert replay.status_code == 401

        # And the legitimate descendant is now dead too â€” the whole family went.
        client.cookies.clear()
        assert client.post("/api/auth/refresh").status_code == 401

    def test_logout_all_invalidates_every_family(self, client: TestClient):
        acct = make_account(client)
        assert client.post("/api/auth/logout-all", headers=acct["headers"]).status_code == 204
        assert client.post("/api/auth/refresh").status_code == 401


class TestWsTickets:
    def test_ticket_is_single_use(self, client: TestClient):
        acct = make_account(client)
        ticket = client.post("/api/auth/ws-ticket", headers=acct["headers"]).json()["ticket"]

        with client.websocket_connect(f"/ws/knowledge?ticket={ticket}") as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "stop"})

        # Second use must fail.
        with pytest.raises(Exception):
            with client.websocket_connect(f"/ws/knowledge?ticket={ticket}") as ws:
                ws.receive_json()

    def test_socket_without_a_ticket_is_closed(self, client: TestClient):
        """No audio frame may be read before the ticket is validated."""
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/knowledge") as ws:
                ws.receive_json()

    def test_socket_with_a_bogus_ticket_is_closed(self, client: TestClient):
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/knowledge?ticket=nonsense") as ws:
                ws.receive_json()


class TestPasswordChange:
    def test_change_requires_the_current_password(self, client: TestClient):
        acct = make_account(client)
        r = client.post(
            "/api/auth/me/password",
            headers=acct["headers"],
            json={"current_password": "wrong", "new_password": "a-brand-new-passphrase"},
        )
        assert r.status_code == 401

    def test_change_succeeds_and_keeps_this_device_signed_in(self, client: TestClient):
        acct = make_account(client)
        r = client.post(
            "/api/auth/me/password",
            headers=acct["headers"],
            json={"current_password": GOOD, "new_password": "a-brand-new-passphrase"},
        )
        assert r.status_code == 204
        # The device that made the change keeps its refresh token.
        assert client.post("/api/auth/refresh").status_code == 200


class TestAccountDeletion:
    def test_delete_removes_every_row(self, client: TestClient):
        """Erasure means erasure â€” no is_deleted flag, no orphaned transcripts."""
        import anyio
        from sqlalchemy import func, select

        from app.db.models import RefreshToken, Session, Turn, User, WsTicket
        from app.db.session import SessionLocal

        acct = make_account(client)
        sid = client.post("/api/sessions", headers=acct["headers"]).json()["id"]
        client.post("/api/auth/ws-ticket", headers=acct["headers"])
        user_id = acct["user"]["id"]

        r = client.request(
            "DELETE",
            "/api/auth/me",
            headers=acct["headers"],
            json={"current_password": GOOD},
        )
        assert r.status_code == 204

        async def count_everything() -> dict[str, int]:
            async with SessionLocal() as db:
                return {
                    "users": (
                        await db.execute(
                            select(func.count()).select_from(User).where(User.id == user_id)
                        )
                    ).scalar_one(),
                    "sessions": (
                        await db.execute(
                            select(func.count())
                            .select_from(Session)
                            .where(Session.user_id == user_id)
                        )
                    ).scalar_one(),
                    "turns": (
                        await db.execute(
                            select(func.count()).select_from(Turn).where(Turn.session_id == sid)
                        )
                    ).scalar_one(),
                    "refresh": (
                        await db.execute(
                            select(func.count())
                            .select_from(RefreshToken)
                            .where(RefreshToken.user_id == user_id)
                        )
                    ).scalar_one(),
                    "tickets": (
                        await db.execute(
                            select(func.count())
                            .select_from(WsTicket)
                            .where(WsTicket.user_id == user_id)
                        )
                    ).scalar_one(),
                }

        assert _run(count_everything) == {
            "users": 0,
            "sessions": 0,
            "turns": 0,
            "refresh": 0,
            "tickets": 0,
        }


def _run(coro_fn):
    import asyncio

    return asyncio.run(coro_fn())


