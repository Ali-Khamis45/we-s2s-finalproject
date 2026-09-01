"""API surface, persistence, and degradation.

Nothing here loads a model. These prove the product behaves correctly in the
state a fresh clone is in, which is also the state the fallback path has to
survive.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestHealth:
    """These three stay public, and must not leak per-user information."""

    def test_health_is_cheap_and_always_up(self, client: TestClient):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_status_reports_capabilities_without_crashing(self, client: TestClient):
        """With no models present this must still answer, not 500."""
        r = client.get("/api/status")
        assert r.status_code == 200
        body = r.json()
        assert body["live_available"] is False  # Moshi disabled in conftest
        assert "corpus_chunks" in body
        assert body["prompt_version"].startswith("a12-")

    def test_acoustic_schema_is_served_for_track_m(self, client: TestClient):
        r = client.get("/api/schema/acoustic")
        assert r.status_code == 200
        body = r.json()
        assert body["version"] == "1.0"
        assert "properties" in body["schema"]


class TestSessions:
    def test_create_and_fetch(self, authed_client: TestClient):
        created = authed_client.post("/api/sessions")
        assert created.status_code == 201
        sid = created.json()["id"]

        fetched = authed_client.get(f"/api/sessions/{sid}")
        assert fetched.status_code == 200
        assert fetched.json()["turn_count"] == 0

    def test_missing_session_returns_error_envelope(self, authed_client: TestClient):
        r = authed_client.get("/api/sessions/does-not-exist")
        assert r.status_code == 404
        error = r.json()["error"]
        assert error["code"] == "not_found"
        # Written for the user, not the developer.
        assert "doesn't exist" in error["message"]
        assert "request_id" in error

    def test_progress_route_is_not_shadowed_by_session_id(self, authed_client: TestClient):
        """/progress is a literal path competing with /{session_id}."""
        r = authed_client.get("/api/sessions/progress")
        assert r.status_code == 200
        assert "points" in r.json()

    def test_end_and_delete(self, authed_client: TestClient):
        sid = authed_client.post("/api/sessions").json()["id"]
        assert authed_client.post(f"/api/sessions/{sid}/end").json()["ended_at"] is not None
        assert authed_client.delete(f"/api/sessions/{sid}").status_code == 204
        assert authed_client.get(f"/api/sessions/{sid}").status_code == 404


class TestDegradation:
    """The product must work with the flagship down. This is the largest risk."""

    def test_chat_reports_model_unavailable_not_a_crash(
        self, authed_client: TestClient, llm_offline: None
    ):
        """Model unreachable: a clear 503 naming the fix, not a stack trace."""
        r = authed_client.post("/api/chat", json={"message": "Hello"})
        assert r.status_code == 503
        error = r.json()["error"]
        assert error["code"] == "model_unavailable"
        assert "llama-server" in error["message"]

    def test_live_socket_refuses_and_names_the_fallback(self, authed_client: TestClient):
        """With Moshi unreachable, the socket must hand over a working path."""
        from starlette.websockets import WebSocketDisconnect

        ticket = authed_client.post("/api/auth/ws-ticket").json()["ticket"]
        try:
            with authed_client.websocket_connect(f"/ws/live?ticket={ticket}") as ws:
                frame = ws.receive_json()
                assert frame["type"] == "mode"
                assert frame["data"]["live_available"] is False
                assert frame["data"]["fallback"] == "/ws/knowledge"
        except WebSocketDisconnect:
            pass  # Expected: the server closes after advertising the fallback.

    def test_knowledge_socket_accepts_when_models_are_absent(
        self, authed_client: TestClient
    ):
        ticket = authed_client.post("/api/auth/ws-ticket").json()["ticket"]
        with authed_client.websocket_connect(f"/ws/knowledge?ticket={ticket}") as ws:
            frame = ws.receive_json()
            assert frame["type"] == "ready"
            assert frame["data"]["mode"] == "knowledge"
            assert frame["data"]["input_sample_rate"] == 16_000
            ws.send_json({"type": "stop"})

    def test_bad_audio_upload_is_rejected_clearly(self, authed_client: TestClient):
        r = authed_client.post(
            "/api/chat/audio",
            files={"audio": ("x.wav", b"not a wav file", "audio/wav")},
        )
        assert r.status_code == 422
        assert "WAV" in r.json()["error"]["message"]


class TestCorpus:
    def test_status_on_empty_corpus(self, authed_client: TestClient):
        r = authed_client.get("/api/corpus")
        assert r.status_code == 200
        assert r.json()["chunks"] == 0

