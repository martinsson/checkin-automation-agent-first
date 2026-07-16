"""
SmoobuBookingGateway adapter tests — parsing of /reservations and the
send-message-to-guest contract, driven by a mocked httpx transport (no network).

The sample payloads mirror the real Smoobu API shape (verified live): bookings
carry hyphenated keys (`guest-name`, `is-blocked-booking`), a `channel` object,
and a `language` code; a successful send answers HTTP 201.
"""

import asyncio
import functools
import json

import httpx
import pytest

from src.adapters.smoobu_bookings import SmoobuBookingGateway
from src.ports.reservations import BookingGatewayError

APT_ID = 3230512

_RESERVATIONS_BODY = {
    "page_count": 1,
    "bookings": [
        {
            "id": 139632572,
            "type": "reservation",
            "arrival": "2026-07-16",
            "departure": "2026-07-19",
            "apartment": {"id": APT_ID, "name": "L'Hippocrate"},
            "channel": {"id": 1, "name": "Airbnb"},
            "guest-name": "Marine Cuenot",
            "firstname": "Marine",
            "lastname": "Cuenot",
            "language": "FR",
            "is-blocked-booking": False,
        },
        {
            # Guest already in-house: arrived days ago, checks out today. Must
            # still be listed so an ad-hoc code can be issued to a current guest.
            "id": 139640000,
            "type": "reservation",
            "arrival": "2026-07-12",
            "departure": "2026-07-16",
            "apartment": {"id": APT_ID, "name": "L'Hippocrate"},
            "channel": {"id": 1, "name": "Airbnb"},
            "guest-name": "Paul Durand",
            "language": "FR",
            "is-blocked-booking": False,
        },
        {
            # Owner calendar block — must be filtered out (not a guest to message).
            "id": 999,
            "type": "block",
            "arrival": "2026-07-20",
            "departure": "2026-07-22",
            "apartment": {"id": APT_ID, "name": "L'Hippocrate"},
            "guest-name": "",
            "is-blocked-booking": True,
        },
    ],
}


def _install_transport(monkeypatch, handler) -> None:
    """Make every httpx.AsyncClient the adapter opens route through `handler`."""
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "AsyncClient", functools.partial(httpx.AsyncClient, transport=transport)
    )


def test_upcoming_arrivals_parses_and_skips_blocks(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/reservations"
        assert request.headers["Api-Key"] == "k"
        # WAF trick: a browser-like UA, not the default httpx one.
        assert "Mozilla" in request.headers["user-agent"]
        assert request.url.params["apartmentId"] == str(APT_ID)
        # Query by departure (still-ongoing stays), not arrival, so guests
        # already in-house stay in the list.
        assert "departureFrom" in request.url.params
        assert "arrivalFrom" not in request.url.params
        return httpx.Response(200, json=_RESERVATIONS_BODY)

    _install_transport(monkeypatch, handler)
    gw = SmoobuBookingGateway(api_key="k", apartment_id=APT_ID)
    res = asyncio.run(gw.upcoming_arrivals(60))

    assert len(res) == 2  # the block is dropped, both guests kept
    ids = {r.booking_id for r in res}
    assert ids == {139632572, 139640000}  # includes the in-house (checkout-today) guest
    r = next(r for r in res if r.booking_id == 139632572)
    assert r.property_id == APT_ID
    assert r.guest_name == "Marine Cuenot"
    assert r.channel == "Airbnb"
    assert r.language == "fr"  # normalised to lowercase
    assert r.source == "smoobu"


def test_send_guest_message_posts_to_send_endpoint_and_accepts_201(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"ok": True})

    _install_transport(monkeypatch, handler)
    gw = SmoobuBookingGateway(api_key="k", apartment_id=APT_ID)
    asyncio.run(gw.send_guest_message(139632572, "your code is 12345678"))

    assert seen["path"] == "/api/reservations/139632572/messages/send-message-to-guest"
    assert seen["body"] == {"messageBody": "your code is 12345678"}


def test_send_guest_message_raises_on_http_error(monkeypatch):
    _install_transport(monkeypatch, lambda request: httpx.Response(403, text="forbidden"))
    gw = SmoobuBookingGateway(api_key="k", apartment_id=APT_ID)
    with pytest.raises(BookingGatewayError):
        asyncio.run(gw.send_guest_message(1, "hi"))


def test_managed_properties_reports_the_apartment():
    gw = SmoobuBookingGateway(api_key="k", apartment_id=APT_ID, apartment_name="Hippocrate")
    assert gw.managed_properties() == [("Hippocrate", APT_ID)]
