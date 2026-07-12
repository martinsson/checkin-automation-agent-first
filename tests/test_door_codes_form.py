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
    # both datetime-local fields are pre-filled; end defaults to noon. The start
    # is time-of-day dependent (clamped up to the current hour in the evening),
    # so we don't assert an exact hour here.
    assert 'name="starts_at" value="20' in resp.text or 'name="starts_at" value="' in resp.text
    assert 'name="ends_at" value="' in resp.text
    assert "T12:00" in resp.text  # default end is noon


def test_form_shows_property_dropdown_with_apartment_names():
    client, _ = _make_client()
    resp = client.get("/door-codes")
    assert resp.status_code == 200
    assert '<select name="property_name"' in resp.text
    # apartment names from the device map appear as options
    assert "Le Fernand" in resp.text
    assert "Terracotta" in resp.text
    # the free-text property input is gone
    assert '<input name="property_name"' not in resp.text


def test_submit_without_person_name_succeeds():
    client, door_lock = _make_client()
    resp = client.post("/door-codes", data={
        "person_name": "",
        "property_name": "Le Fernand",
        "starts_at": "2035-07-11T14:00",
        "ends_at": "2035-07-12T12:00",
    })
    assert resp.status_code == 200
    assert len(door_lock.created) == 1
    created = door_lock.created[0]
    assert created.person_name == ""
    # the lock-app label falls back to the property when no name is given
    assert created.code_name == "Le Fernand"


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
        "starts_at": "2035-07-11T14:00",
        "ends_at": "2035-07-12T12:00",
    })
    assert resp.status_code == 200
    assert len(door_lock.created) == 1
    created = door_lock.created[0]
    assert created.person_name == "Plombier Dupont"
    assert created.purpose == "manual"
    assert created.reservation_id is None
    assert created.starts_at == "2035-07-11T14:00:00"
    assert created.ends_at == "2035-07-12T12:00:00"
    # The PIN is displayed to the owner
    assert door_lock.created and "10010000" in resp.text


def test_submit_rounds_to_full_hours():
    client, door_lock = _make_client()
    resp = client.post("/door-codes", data={
        "person_name": "Électricien",
        "starts_at": "2035-07-11T14:20",
        "ends_at": "2035-07-11T17:05",
    })
    assert resp.status_code == 200
    created = door_lock.created[0]
    assert created.starts_at == "2035-07-11T14:00:00"  # rounded down
    assert created.ends_at == "2035-07-11T18:00:00"    # rounded up


def test_submit_rejects_end_before_start():
    client, door_lock = _make_client()
    resp = client.post("/door-codes", data={
        "person_name": "X",
        "starts_at": "2035-07-12T14:00",
        "ends_at": "2035-07-11T12:00",
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
        "starts_at": "2035-07-11T14:00",
        "ends_at": "2035-07-12T12:00",
    })
    assert resp.status_code == 400
    assert "lock offline" in resp.text


def test_submit_without_gateway_configured_shows_error():
    client, _ = _make_client(door_lock=None)
    resp = client.post("/door-codes", data={
        "person_name": "Plombier",
        "starts_at": "2035-07-11T14:00",
        "ends_at": "2035-07-12T12:00",
    })
    assert resp.status_code == 400
    assert "MAKE_IGLOOHOME_WEBHOOK_URL" in resp.text


def test_round_to_hours_keeps_exact_hours():
    starts, ends = _round_to_hours("2035-07-11T14:00", "2035-07-12T12:00")
    assert starts == "2035-07-11T14:00:00"
    assert ends == "2035-07-12T12:00:00"


def test_submit_resolves_device_id_from_property():
    client, door_lock = _make_client()
    resp = client.post("/door-codes", data={
        "person_name": "Plombier",
        "property_name": "Le Fernand",
        "starts_at": "2035-07-11T14:00",
        "ends_at": "2035-07-12T12:00",
    })
    assert resp.status_code == 200
    assert door_lock.created[0].device_id == "EK1X152a8431"


def test_submit_unknown_property_uses_default_device():
    client, door_lock = _make_client()
    resp = client.post("/door-codes", data={
        "person_name": "Plombier",
        "property_name": "Nonexistent Place",
        "starts_at": "2035-07-11T14:00",
        "ends_at": "2035-07-12T12:00",
    })
    assert resp.status_code == 200
    assert door_lock.created[0].device_id == ""  # default is empty in the shipped map


def test_device_map_case_insensitive_and_default():
    from src.config.device_map import DeviceMap
    m = DeviceMap(default="DFLT", properties={"Le Matisse": "EK1X15cbb024"})
    assert m.device_for("le matisse") == "EK1X15cbb024"
    assert m.device_for("unknown") == "DFLT"
    assert m.device_for("") == "DFLT"


# -- start clamping (past windows) ------------------------------------------

def test_clamp_start_leaves_future_untouched():
    from datetime import datetime
    from src.web.door_codes import _clamp_start
    now_hour = datetime(2035, 7, 11, 20, 0, 0)
    assert _clamp_start("2035-07-11T22:00:00", now_hour) == "2035-07-11T22:00:00"


def test_clamp_start_raises_past_to_current_hour():
    from datetime import datetime
    from src.web.door_codes import _clamp_start
    now_hour = datetime(2035, 7, 11, 20, 0, 0)
    # a start earlier than the current hour is pulled up to it
    assert _clamp_start("2035-07-11T14:00:00", now_hour) == "2035-07-11T20:00:00"


def test_default_window_start_is_never_in_the_past():
    from datetime import datetime
    from src.web.door_codes import _default_window, _paris_now
    start_str, end_str = _default_window()
    start = datetime.strptime(start_str, "%Y-%m-%dT%H:%M")
    now = _paris_now().replace(tzinfo=None)
    # start is at or after the current hour, and the window is non-empty
    assert start >= now.replace(minute=0, second=0, microsecond=0)
    assert end_str > start_str


def test_submit_past_window_is_rejected_cleanly():
    # both start and end in the past → clamp makes end <= start → friendly 400,
    # never reaches the gateway (so Igloohome never errors on a past window)
    client, door_lock = _make_client()
    resp = client.post("/door-codes", data={
        "person_name": "Plombier",
        "property_name": "Le Fernand",
        "starts_at": "2000-01-01T14:00",
        "ends_at": "2000-01-01T16:00",
    })
    assert resp.status_code == 400
    assert len(door_lock.created) == 0
    assert "already over" in resp.text
