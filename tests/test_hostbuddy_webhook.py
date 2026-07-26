"""
Tests for the HostBuddy webhook endpoint.

Uses FastAPI TestClient with a stub agent to avoid Claude API calls.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from src.adapters.sqlite_memory import SqliteRequestMemory
from src.web.app import create_app


class _StubAgent:
    """Agent stub that records calls without hitting Claude."""

    def __init__(self):
        self.calls: list[dict] = []

    async def run(self, reservation_id, event_type, event_payload, **kwargs):
        self.calls.append(
            {"reservation_id": reservation_id, "event_type": event_type, **kwargs}
        )


class _StubDepartureService:
    """Records departure handoffs without enrichment/LLM/email."""

    def __init__(self, status="sent"):
        self.calls: list = []
        self._status = status

    async def handle(self, payload):
        self.calls.append(payload)
        return {"status": self._status}


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
    # Override memory with in-memory sqlite and inject stub agent + departure svc
    app.state.memory = SqliteRequestMemory(":memory:")
    stub_agent = _StubAgent()
    app.state.agent = stub_agent
    app.state.departure_service = _StubDepartureService()
    return app, stub_agent


_VALID_PAYLOAD = {
    "action_item_id": "ai-123",
    "booking_id": "42",
    "category": "early_checkin",
    "guest_name": "Alice",
    "property_name": "La Maison",
    "message_summary": "Can I check in at 11am?",
}


def test_valid_early_checkin_webhook():
    app, stub_agent = _make_test_app()
    client = TestClient(app, raise_server_exceptions=True)

    resp = client.post("/webhook/hostbuddy", json=_VALID_PAYLOAD)

    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    assert len(stub_agent.calls) == 1
    call = stub_agent.calls[0]
    assert call["reservation_id"] == 42
    assert call["event_type"] == "hostbuddy_action_item"


def test_valid_late_checkout_webhook():
    app, stub_agent = _make_test_app()
    client = TestClient(app, raise_server_exceptions=True)

    payload = {**_VALID_PAYLOAD, "category": "late_checkout", "action_item_id": "ai-456"}
    resp = client.post("/webhook/hostbuddy", json=payload)

    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


def test_ignored_category():
    app, stub_agent = _make_test_app()
    client = TestClient(app, raise_server_exceptions=True)

    payload = {**_VALID_PAYLOAD, "category": "noise_complaint", "action_item_id": "ai-789"}
    resp = client.post("/webhook/hostbuddy", json=payload)

    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    assert len(stub_agent.calls) == 0


def test_malformed_payload_missing_booking_id():
    app, stub_agent = _make_test_app()
    client = TestClient(app, raise_server_exceptions=True)

    payload = {k: v for k, v in _VALID_PAYLOAD.items() if k != "booking_id"}
    resp = client.post("/webhook/hostbuddy", json=payload)

    assert resp.status_code == 422
    assert len(stub_agent.calls) == 0


def test_duplicate_action_item_ignored():
    app, stub_agent = _make_test_app()
    client = TestClient(app, raise_server_exceptions=True)

    # First delivery
    resp1 = client.post("/webhook/hostbuddy", json=_VALID_PAYLOAD)
    assert resp1.json()["status"] == "accepted"

    # Second delivery with same action_item_id
    resp2 = client.post("/webhook/hostbuddy", json=_VALID_PAYLOAD)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "duplicate"

    # Agent only called once
    assert len(stub_agent.calls) == 1


# -- departure categories → one-way cleaner notification flow -----------------

_DEPARTURE_PAYLOAD = {
    "action_item_id": "dep-1",
    "booking_id": "77",
    "category": "guest_left",
    "guest_name": "Bob",
    "property_name": "Le Fernand",
    "message_summary": "We've just left the apartment, keys on the table.",
}


def test_guest_left_routes_to_departure_service():
    app, stub_agent = _make_test_app()
    client = TestClient(app, raise_server_exceptions=True)

    resp = client.post("/webhook/hostbuddy", json=_DEPARTURE_PAYLOAD)

    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"
    dep = app.state.departure_service
    assert len(dep.calls) == 1
    assert dep.calls[0].booking_id == 77
    assert dep.calls[0].category == "guest_left"
    # The early/late-checkout agent must NOT be involved.
    assert len(stub_agent.calls) == 0


def test_early_departure_routes_to_departure_service():
    app, _ = _make_test_app()
    client = TestClient(app, raise_server_exceptions=True)

    payload = {**_DEPARTURE_PAYLOAD, "category": "early_departure", "action_item_id": "dep-2"}
    resp = client.post("/webhook/hostbuddy", json=payload)

    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"
    assert app.state.departure_service.calls[0].category == "early_departure"


def test_departure_duplicate_delivery_not_reprocessed():
    app, _ = _make_test_app()
    client = TestClient(app, raise_server_exceptions=True)

    r1 = client.post("/webhook/hostbuddy", json=_DEPARTURE_PAYLOAD)
    r2 = client.post("/webhook/hostbuddy", json=_DEPARTURE_PAYLOAD)

    assert r1.json()["status"] == "sent"
    assert r2.json()["status"] == "duplicate"
    assert len(app.state.departure_service.calls) == 1


def test_departure_service_unavailable_returns_503():
    app, _ = _make_test_app()
    app.state.departure_service = None
    client = TestClient(app, raise_server_exceptions=True)

    resp = client.post("/webhook/hostbuddy", json=_DEPARTURE_PAYLOAD)
    assert resp.status_code == 503
