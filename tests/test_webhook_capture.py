"""
Tests for raw webhook capture (WebhookCaptureMiddleware + GET /captures).

The point of capture is to record every HostBuddy delivery verbatim so we can
reverse-engineer the (undocumented) payload shapes. These tests pin the three
things that matter: every /webhook/ POST is stored, storage never swallows the
request, and the viewer is behind auth.
"""

import pytest
from fastapi.testclient import TestClient

from src.adapters.sqlite_memory import SqliteRequestMemory
from src.web.app import create_app


class _StubAgent:
    def __init__(self):
        self.calls: list[dict] = []

    async def run(self, reservation_id, event_type, event_payload, **kwargs):
        self.calls.append({"reservation_id": reservation_id, "event_type": event_type})


class _StubDepartureService:
    def __init__(self):
        self.calls: list = []

    async def handle(self, payload):
        self.calls.append(payload)
        return {"status": "sent"}


def _make_test_app():
    import os
    os.environ.setdefault("REVIEW_TOKEN", "test-token")
    os.environ.setdefault("DB_PATH", ":memory:")
    os.environ.setdefault("SMTP_HOST", "localhost")
    os.environ.setdefault("SMTP_USER", "test@test.com")
    os.environ.setdefault("SMTP_PASSWORD", "x")
    os.environ.setdefault("IMAP_HOST", "localhost")
    os.environ.setdefault("CLEANER_EMAIL", "cleaner@test.com")
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

    app = create_app()
    memory = SqliteRequestMemory(":memory:")
    app.state.memory = memory
    app.state.agent = _StubAgent()
    app.state.departure_service = _StubDepartureService()
    return app, memory


_PAYLOAD = {
    "action_item_id": "ai-cap-1",
    "booking_id": "42",
    "category": "early_checkin",
    "guest_name": "Alice",
    "property_name": "La Maison",
    "message_summary": "Can I check in at 11am?",
}


@pytest.mark.asyncio
async def test_valid_post_is_captured_verbatim():
    app, memory = _make_test_app()
    client = TestClient(app)

    resp = client.post("/webhook/hostbuddy", json=_PAYLOAD)
    assert resp.status_code == 200

    captures = await memory.get_webhook_captures()
    assert len(captures) == 1
    cap = captures[0]
    assert cap.path == "/webhook/hostbuddy"
    assert cap.method == "POST"
    assert '"action_item_id": "ai-cap-1"' in cap.body or "ai-cap-1" in cap.body
    assert cap.headers.get("content-type", "").startswith("application/json")


@pytest.mark.asyncio
async def test_capture_does_not_break_downstream_handler():
    """Body is replayed, so the real handler still fires the agent."""
    app, memory = _make_test_app()
    client = TestClient(app)

    resp = client.post("/webhook/hostbuddy", json=_PAYLOAD)
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    assert len(app.state.agent.calls) == 1  # downstream handler ran


@pytest.mark.asyncio
async def test_invalid_body_is_still_captured():
    """A body that fails validation must still be stored — that's exactly the
    case we want to inspect when a field gets renamed."""
    app, memory = _make_test_app()
    client = TestClient(app)

    resp = client.post(
        "/webhook/hostbuddy",
        content=b"not json at all",
        headers={"content-type": "text/plain"},
    )
    assert resp.status_code == 422  # handler rejects it...

    captures = await memory.get_webhook_captures()
    assert len(captures) == 1  # ...but we captured it anyway
    assert captures[0].body == "not json at all"


@pytest.mark.asyncio
async def test_non_webhook_post_is_not_captured():
    app, memory = _make_test_app()
    client = TestClient(app)

    client.post("/login", data={"username": "johan", "password": "wrong"})

    captures = await memory.get_webhook_captures()
    assert captures == []


@pytest.mark.asyncio
async def test_secret_headers_are_redacted():
    app, memory = _make_test_app()
    client = TestClient(app)

    client.post(
        "/webhook/hostbuddy",
        json=_PAYLOAD,
        headers={"authorization": "Bearer super-secret"},
    )

    cap = (await memory.get_webhook_captures())[0]
    assert cap.headers.get("authorization") == "<redacted>"


def test_captures_viewer_requires_auth():
    app, _ = _make_test_app()
    client = TestClient(app)

    resp = client.get("/captures", follow_redirects=False)
    # AuthMiddleware redirects unauthenticated GETs to /login.
    assert resp.status_code in (302, 307)
    assert "/login" in resp.headers.get("location", "")


def test_captures_viewer_returns_stored_captures():
    app, _ = _make_test_app()
    client = TestClient(app)
    client.post("/webhook/hostbuddy", json=_PAYLOAD)

    client.cookies.set("session", "test-token")  # authenticate as owner
    resp = client.get("/captures")
    assert resp.status_code == 200

    body = resp.json()
    assert body["count"] == 1
    entry = body["captures"][0]
    assert entry["path"] == "/webhook/hostbuddy"
    assert entry["category"] == "early_checkin"
    assert entry["body_json"]["action_item_id"] == "ai-cap-1"
