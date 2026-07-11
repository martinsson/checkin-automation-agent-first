"""
Tests for the /door-codes owner form (manual Igloohome code creation).
"""

import os

from fastapi.testclient import TestClient

from src.adapters.simulator_door_lock import SimulatorDoorLockGateway
from src.web.app import create_app
from src.web.door_codes import _round_to_hours


_DEFAULT = object()


def _make_client(door_lock=_DEFAULT):
    if door_lock is _DEFAULT:
        door_lock = SimulatorDoorLockGateway()
    os.environ.setdefault("REVIEW_TOKEN", "test-token")
    os.environ.setdefault("DB_PATH", ":memory:")
    os.environ.setdefault("SMTP_HOST", "localhost")
    os.environ.setdefault("SMTP_USER", "test@test.com")
    os.environ.setdefault("SMTP_PASSWORD", "x")
    os.environ.setdefault("IMAP_HOST", "localhost")
    os.environ.setdefault("CLEANER_EMAIL", "cleaner@test.com")
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

    app = create_app()
    app.state.door_lock = door_lock
    client = TestClient(app)
    client.cookies.set("session", "test-token")  # authenticated owner
    return client, door_lock


def test_form_page_shows_default_window():
    client, _ = _make_client()
    resp = client.get("/door-codes")
    assert resp.status_code == 200
    assert 'name="starts_at" value="' in resp.text
    assert "T14:00" in resp.text  # default start today 14:00
    assert "T12:00" in resp.text  # default end tomorrow 12:00


def test_form_requires_login():
    client, _ = _make_client()
    client.cookies.clear()
    resp = client.get("/door-codes", follow_redirects=False)
    assert resp.status_code in (303, 307)
    assert resp.headers["location"] == "/login"


def test_submit_creates_code_and_shows_pin():
    client, door_lock = _make_client()
    resp = client.post("/door-codes", data={
        "person_name": "Plombier Dupont",
        "property_name": "",
        "starts_at": "2026-07-11T14:00",
        "ends_at": "2026-07-12T12:00",
    })
    assert resp.status_code == 200
    assert len(door_lock.created) == 1
    created = door_lock.created[0]
    assert created.person_name == "Plombier Dupont"
    assert created.purpose == "manual"
    assert created.reservation_id is None
    assert created.starts_at == "2026-07-11T14:00:00"
    assert created.ends_at == "2026-07-12T12:00:00"
    # The PIN is displayed to the owner
    assert door_lock.created and "10010000" in resp.text


def test_submit_rounds_to_full_hours():
    client, door_lock = _make_client()
    resp = client.post("/door-codes", data={
        "person_name": "Électricien",
        "starts_at": "2026-07-11T14:20",
        "ends_at": "2026-07-11T17:05",
    })
    assert resp.status_code == 200
    created = door_lock.created[0]
    assert created.starts_at == "2026-07-11T14:00:00"  # rounded down
    assert created.ends_at == "2026-07-11T18:00:00"    # rounded up


def test_submit_rejects_end_before_start():
    client, door_lock = _make_client()
    resp = client.post("/door-codes", data={
        "person_name": "X",
        "starts_at": "2026-07-12T14:00",
        "ends_at": "2026-07-11T12:00",
    })
    assert resp.status_code == 400
    assert len(door_lock.created) == 0
    assert "end must be after the start" in resp.text


def test_submit_shows_gateway_error_in_form():
    gateway = SimulatorDoorLockGateway()
    gateway.fail_with = "lock offline"
    client, _ = _make_client(gateway)
    resp = client.post("/door-codes", data={
        "person_name": "Plombier",
        "starts_at": "2026-07-11T14:00",
        "ends_at": "2026-07-12T12:00",
    })
    assert resp.status_code == 400
    assert "lock offline" in resp.text


def test_submit_without_gateway_configured_shows_error():
    client, _ = _make_client(door_lock=None)
    resp = client.post("/door-codes", data={
        "person_name": "Plombier",
        "starts_at": "2026-07-11T14:00",
        "ends_at": "2026-07-12T12:00",
    })
    assert resp.status_code == 400
    assert "MAKE_IGLOOHOME_WEBHOOK_URL" in resp.text


def test_round_to_hours_keeps_exact_hours():
    starts, ends = _round_to_hours("2026-07-11T14:00", "2026-07-12T12:00")
    assert starts == "2026-07-11T14:00:00"
    assert ends == "2026-07-12T12:00:00"
