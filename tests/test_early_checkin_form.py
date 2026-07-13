"""
Tests for the /early-checkin owner form (per-guest Igloohome code + send).
"""

import os

from fastapi.testclient import TestClient

from src.adapters.simulator_door_lock import SimulatorDoorLockGateway
from src.ports.reservations import BookingGatewayError, GuestBookingGateway, Reservation
from src.web.app import create_app
from src.web.early_checkin import _compose_message


class FakeBookingGateway(GuestBookingGateway):
    def __init__(self, reservations=None, fail_send=False):
        self._reservations = reservations or []
        self.fail_send = fail_send
        self.sent: list[tuple[int, str]] = []

    async def upcoming_arrivals(self, days: int):
        return list(self._reservations)

    async def send_guest_message(self, booking_id: int, message: str) -> None:
        if self.fail_send:
            raise BookingGatewayError("boom")
        self.sent.append((booking_id, message))


def _sim_pin(n: int, reservation_id: int) -> str:
    """The deterministic PIN SimulatorDoorLockGateway returns for the n-th code."""
    return f"{1000 + n:04d}{(reservation_id or 0) % 10000:04d}"


# La Palma = 326275 (see config/beds24_properties.yaml)
_RES = [
    Reservation(
        booking_id=99001,
        property_id=326275,
        guest_name="Alice Martin",
        arrival="2026-08-03",
        departure="2026-08-10",
        channel="airbnb",
        status="confirmed",
    )
]


def _make_client(door_lock=None, gateway=None):
    os.environ.setdefault("REVIEW_TOKEN", "test-token")
    os.environ.setdefault("DB_PATH", ":memory:")
    os.environ.setdefault("SMTP_HOST", "localhost")
    os.environ.setdefault("SMTP_USER", "test@test.com")
    os.environ.setdefault("SMTP_PASSWORD", "x")
    os.environ.setdefault("IMAP_HOST", "localhost")
    os.environ.setdefault("CLEANER_EMAIL", "cleaner@test.com")
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

    app = create_app()
    app.state.door_lock = door_lock or SimulatorDoorLockGateway()
    app.state.booking_gateway = gateway if gateway is not None else FakeBookingGateway(_RES)
    client = TestClient(app)
    client.cookies.set("session", "test-token")
    return client, app.state.door_lock, app.state.booking_gateway


def test_form_lists_properties_and_embeds_reservations():
    client, _, _ = _make_client()
    resp = client.get("/early-checkin")
    assert resp.status_code == 200
    # property dropdown from the beds24 property map
    assert 'data-pid="326275"' in resp.text and "La Palma" in resp.text
    # reservations embedded as JSON keyed by propertyId for the client filter
    assert "99001" in resp.text and "Alice Martin" in resp.text
    # two submit actions
    assert 'value="create_send"' in resp.text and 'value="create"' in resp.text


def test_create_only_makes_code_without_sending():
    client, door_lock, gateway = _make_client()
    resp = client.post(
        "/early-checkin",
        data={
            "action": "create",
            "property_name": "La Palma",
            "reservation_id": "99001",
            "guest_name": "Alice Martin",
            "start_date": "2026-08-03",
            "start_hour": "14",
            "end_date": "2026-08-10",
            "end_hour": "12",
        },
    )
    assert resp.status_code == 200
    assert len(door_lock.created) == 1
    assert gateway.sent == []  # nothing sent
    assert "Code created" in resp.text


def test_create_and_send_messages_the_guest():
    client, door_lock, gateway = _make_client()
    resp = client.post(
        "/early-checkin",
        data={
            "action": "create_send",
            "property_name": "La Palma",
            "reservation_id": "99001",
            "guest_name": "Alice Martin",
            "start_date": "2026-08-03",
            "start_hour": "14",
            "end_date": "2026-08-10",
            "end_hour": "12",
        },
    )
    assert resp.status_code == 200
    assert len(door_lock.created) == 1
    assert len(gateway.sent) == 1
    booking_id, message = gateway.sent[0]
    assert booking_id == 99001
    pin = _sim_pin(1, 99001)
    assert pin in message
    # message states validity and that it replaces the automatic code
    assert "03/08/2026 14:00" in message and "10/08/2026 12:00" in message
    assert "instead of" in message and "plutôt que" in message
    assert "Sent to" in resp.text


def test_send_failure_still_shows_code_with_error():
    client, door_lock, _ = _make_client(gateway=FakeBookingGateway(_RES, fail_send=True))
    resp = client.post(
        "/early-checkin",
        data={
            "action": "create_send",
            "property_name": "La Palma",
            "reservation_id": "99001",
            "guest_name": "Alice Martin",
            "start_date": "2026-08-03",
            "start_hour": "14",
            "end_date": "2026-08-10",
            "end_hour": "12",
        },
    )
    assert resp.status_code == 200
    assert len(door_lock.created) == 1  # code still created
    assert "sending failed" in resp.text.lower()
    assert _sim_pin(1, 99001) in resp.text  # code shown despite failure


def test_missing_reservation_is_rejected():
    client, door_lock, _ = _make_client()
    resp = client.post(
        "/early-checkin",
        data={
            "action": "create_send",
            "property_name": "La Palma",
            "reservation_id": "",
            "start_date": "2026-08-03",
            "start_hour": "14",
            "end_date": "2026-08-10",
            "end_hour": "12",
        },
    )
    assert resp.status_code == 400
    assert door_lock.created == []


def test_compose_message_is_bilingual_and_names_the_code():
    msg = _compose_message("12345678", "2026-08-03T14:00:00", "2026-08-10T12:00:00")
    assert "12345678" in msg
    assert "Bonjour" in msg and "Hello" in msg
    assert "03/08/2026 14:00" in msg and "10/08/2026 12:00" in msg
