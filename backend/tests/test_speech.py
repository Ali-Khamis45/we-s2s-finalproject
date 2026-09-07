"""Replaying a stored coach reply as speech.

The endpoint reads its text from the turn row, never from the request, so it
cannot be used as a general text-to-speech service by anyone holding a token.
These tests pin that down alongside the ownership boundary and the rate rule,
which is the part that carries the project's argument: a replay has to sound
the way the reply sounded when it was produced, not at a flat default.
"""

from __future__ import annotations

import json
import sqlite3
import struct
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import make_account


def seed_turn(
    session_id: str,
    role: str,
    text: str,
    acoustic: dict[str, Any] | None = None,
) -> int:
    """Insert a turn directly and return its id.

    Direct insertion rather than POST /api/chat: a turn is all these tests
    need, and the chat route would require a served model.

    Plain sqlite3 rather than the app's async session. `asyncio.run()` closes
    the loop it creates, and SQLAlchemy's aiosqlite pool binds connections to
    the loop they were opened on — reusing that pool afterwards raises
    "Event loop is closed". Writing synchronously touches no loop and no pool,
    so this helper cannot disturb whichever test runs next.
    """
    from app.core.config import settings

    path = settings.database_url.split("///", 1)[1]
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")

    with sqlite3.connect(path) as conn:
        cur = conn.execute(
            "INSERT INTO turns (session_id, created_at, role, mode, text, acoustic)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                session_id,
                now,
                role,
                "knowledge",
                text,
                json.dumps(acoustic) if acoustic is not None else None,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


@pytest.fixture(autouse=True)
def _clean_limiter():
    from app.services.auth import limiter

    limiter.clear()
    yield
    limiter.clear()


@pytest.fixture
def fake_tts(monkeypatch):
    """Replace synthesis, and record what rate it was asked for.

    Kokoro downloads a voice pack on first use, so the real service has no
    place in a unit test. The recording is the point: test 4 asserts on the
    rate that reached the service, which is the behaviour under test.
    """
    from app.services import tts as tts_module

    calls: list[dict[str, Any]] = []

    async def fake_synthesize(self, text: str, *, speed: float | None = None):
        calls.append({"text": text, "speed": speed})
        # A quarter second of quiet, enough to encode a real WAV.
        return np.zeros(6000, dtype=np.float32), 24_000

    monkeypatch.setattr(
        tts_module.TTSService, "synthesize", fake_synthesize, raising=True
    )
    from app.services.speech_replay import replay_cache

    replay_cache.clear()
    return calls


@pytest.fixture
def session_id(authed_client: TestClient) -> str:
    return authed_client.post("/api/sessions").json()["id"]


def url(sid: str, tid: int) -> str:
    return f"/api/sessions/{sid}/turns/{tid}/speech"


class TestReplayEndpoint:
    def test_coach_turn_returns_a_wav(
        self, authed_client: TestClient, session_id: str, fake_tts
    ):
        tid = seed_turn(session_id, "coach", "Let the first sentence be short.")

        r = authed_client.get(url(session_id, tid))

        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "audio/wav"
        assert r.content[:4] == b"RIFF" and r.content[8:12] == b"WAVE"
        # Header declares the sample rate the client will play it back at.
        assert struct.unpack_from("<I", r.content, 24)[0] == 24_000

    def test_another_account_gets_404_not_audio(
        self, client: TestClient, authed_client: TestClient, session_id: str, fake_tts
    ):
        tid = seed_turn(session_id, "coach", "Take your time.")
        stranger = make_account(client)

        r = client.get(url(session_id, tid), headers=stranger["headers"])

        assert r.status_code == 404
        assert not r.content.startswith(b"RIFF")

    def test_a_user_turn_is_not_read_back(
        self, authed_client: TestClient, session_id: str, fake_tts
    ):
        tid = seed_turn(session_id, "user", "I get nervous before presentations.")

        r = authed_client.get(url(session_id, tid))

        assert r.status_code == 422

    def test_missing_turn_is_404(
        self, authed_client: TestClient, session_id: str, fake_tts
    ):
        assert authed_client.get(url(session_id, 999_999)).status_code == 404

    def test_rate_comes_from_the_preceding_user_turn(
        self, authed_client: TestClient, session_id: str, fake_tts
    ):
        """The replay must sound the way the reply was delivered.

        A 1500 ms block in the speaker's audio slowed the coach down when this
        turn was first spoken; replaying it at 1.0 would misrepresent both the
        moment and the feature.
        """
        seed_turn(
            session_id,
            "user",
            "I, I, I want water",
            acoustic={
                "schema_version": "1.0",
                "duration_ms": 3158,
                "events": [
                    {
                        "kind": "block",
                        "start_ms": 1200,
                        "end_ms": 2700,
                        "confidence": 1.0,
                    }
                ],
                "prosody": {"speech_rate_wpm": 98.7, "longest_pause_ms": 1500},
                "analyzed": True,
                "source": "heuristic",
            },
        )
        tid = seed_turn(session_id, "coach", "Take a breath. Short sentence next.")

        assert authed_client.get(url(session_id, tid)).status_code == 200
        assert fake_tts[-1]["speed"] < 1.0, "a heard block should slow the replay"

    def test_typed_turn_replays_at_the_default_rate(
        self, authed_client: TestClient, session_id: str, fake_tts
    ):
        seed_turn(session_id, "user", "How do I use pauses?", acoustic=None)
        tid = seed_turn(session_id, "coach", "Pause after each new idea.")

        assert authed_client.get(url(session_id, tid)).status_code == 200
        assert fake_tts[-1]["speed"] == 1.0

    def test_second_request_is_served_from_cache(
        self, authed_client: TestClient, session_id: str, fake_tts
    ):
        tid = seed_turn(session_id, "coach", "One suggestion at a time.")

        first = authed_client.get(url(session_id, tid))
        second = authed_client.get(url(session_id, tid))

        assert first.status_code == second.status_code == 200
        assert first.content == second.content
        assert len(fake_tts) == 1, "the button invites repeat presses"

    def test_voice_unavailable_returns_503_in_the_error_envelope(
        self, authed_client: TestClient, session_id: str, monkeypatch, fake_tts
    ):
        from app.core.errors import DependencyMissingError
        from app.services import tts as tts_module

        async def missing(self, text: str, *, speed: float | None = None):
            raise DependencyMissingError("kokoro", "Text-to-speech")

        monkeypatch.setattr(tts_module.TTSService, "synthesize", missing)
        tid = seed_turn(session_id, "coach", "Ready when you are.")

        r = authed_client.get(url(session_id, tid))

        assert r.status_code == 503
        assert r.json()["error"]["code"]

    def test_an_empty_reply_has_nothing_to_say(
        self, authed_client: TestClient, session_id: str, fake_tts
    ):
        tid = seed_turn(session_id, "coach", "   ")

        assert authed_client.get(url(session_id, tid)).status_code == 422

    def test_a_turn_from_another_session_is_not_reachable(
        self, authed_client: TestClient, session_id: str, fake_tts
    ):
        """The turn id must be checked against the session in the path.

        Turn ids are a global autoincrement, so without this check any signed-in
        user could walk the whole table through a session they do own.
        """
        other = authed_client.post("/api/sessions").json()["id"]
        tid = seed_turn(other, "coach", "Not reachable from the first session.")

        assert authed_client.get(url(session_id, tid)).status_code == 404
