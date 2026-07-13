"""
Tests for the /early-checkin owner flow: create the code, then review/edit the
pre-filled message and send it (two steps, one primary button each).
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
        language="fr",
    )
]

_CREATE = {
    "property_name": "La Palma",
    "reservation_id": "99001",
    "guest_name": "Alice Martin",
    "guest_language": "fr",
    "start_date": "2026-08-03",
    "start_hour": "14",
    "end_date": "2026-08-10",
    "end_hour": "12",
}


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
    assert 'data-pid="326275"' in resp.text and "La Palma" in resp.text
    assert "99001" in resp.text and "Alice Martin" in resp.text
    # a single primary button, and the language is carried for the message
    assert ">Create code</button>" in resp.text
    assert 'value="create_send"' not in resp.text
    assert 'name="guest_language"' in resp.text


def test_create_shows_code_and_prefilled_message_without_sending():
    client, door_lock, gateway = _make_client()
    resp = client.post("/early-checkin", data=_CREATE)
    assert resp.status_code == 200
    assert len(door_lock.created) == 1
    assert gateway.sent == []  # create does NOT send
    pin = _sim_pin(1, 99001)
    assert pin in resp.text  # code shown
    # the editable message is pre-filled (French, since guest lang = fr) with the PIN
    assert "<textarea" in resp.text and "Send to guest" in resp.text
    assert "Voici votre code" in resp.text and pin in resp.text
    assert "Message to" in resp.text and "(French)" in resp.text


def test_send_delivers_the_edited_message():
    client, _, gateway = _make_client()
    # step 1: create
    client.post("/early-checkin", data=_CREATE)
    # step 2: send an (edited) message
    edited = "Bonjour Alice, voici votre code : 12345678. Bonne arrivée !"
    resp = client.post(
        "/early-checkin/send",
        data={"reservation_id": "99001", "guest_name": "Alice Martin", "message": edited},
    )
    assert resp.status_code == 200
    assert gateway.sent == [(99001, edited)]  # exactly what was in the textarea
    assert "Sent" in resp.text


def test_send_failure_keeps_the_message_for_retry():
    client, _, _ = _make_client(gateway=FakeBookingGateway(_RES, fail_send=True))
    msg = "Bonjour, code 12345678"
    resp = client.post(
        "/early-checkin/send",
        data={"reservation_id": "99001", "guest_name": "Alice Martin", "message": msg},
    )
    assert resp.status_code == 200
    assert "sending failed" in resp.text.lower()
    assert msg in resp.text  # message preserved so the owner can retry
    assert "Send to guest" in resp.text


def test_missing_reservation_is_rejected_on_create():
    client, door_lock, _ = _make_client()
    resp = client.post("/early-checkin", data={**_CREATE, "reservation_id": ""})
    assert resp.status_code == 400
    assert door_lock.created == []


def test_compose_message_french_for_french_guest():
    msg = _compose_message("12345678", "2026-08-03T14:00:00", "2026-08-10T12:00:00", "fr")
    assert "12345678" in msg
    assert "Bonjour" in msg and "plutôt que" in msg
    assert "Hello" not in msg  # French only
    assert "03/08/2026 14:00" in msg and "10/08/2026 12:00" in msg


def test_compose_message_english_otherwise():
    for lang in ("en", "de", ""):
        msg = _compose_message("12345678", "2026-08-03T14:00:00", "2026-08-10T12:00:00", lang)
        assert "Hello" in msg and "instead of" in msg
        assert "Bonjour" not in msg  # English only
