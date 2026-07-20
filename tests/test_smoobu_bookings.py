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
        return httpx.Response(200, json=_RESERVATIONS_BODY)

    _install_transport(monkeypatch, handler)
    gw = SmoobuBookingGateway(api_key="k", apartment_id=APT_ID)
    res = asyncio.run(gw.upcoming_arrivals(60))

    assert len(res) == 1  # the block is dropped
    r = res[0]
    assert r.booking_id == 139632572
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


def test_stays_overlapping_keeps_blocks_and_filters_by_window(monkeypatch):
    from datetime import date

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=_RESERVATIONS_BODY)

    _install_transport(monkeypatch, handler)
    gw = SmoobuBookingGateway(api_key="k", apartment_id=APT_ID)
    res = asyncio.run(gw.stays_overlapping(date(2026, 7, 16), date(2026, 7, 26)))

    # Window is translated to arrivalTo = last night, departureFrom = window start.
    assert seen["params"]["arrivalTo"] == "2026-07-25"
    assert seen["params"]["departureFrom"] == "2026-07-16"
    # Unlike upcoming_arrivals, blocks are KEPT (a blocked night is not free).
    assert len(res) == 2
    block = next(r for r in res if r.booking_id == 999)
    assert block.status == "block" and block.guest_name == "Blocked"


def test_bookings_changed_since_includes_cancellations(monkeypatch):
    from datetime import datetime

    seen = {}
    body = {
        "page_count": 1,
        "bookings": [
            {
                "id": 777,
                "type": "cancellation",
                "arrival": "2026-07-23",
                "departure": "2026-07-26",
                "apartment": {"id": APT_ID, "name": "L'Hippocrate"},
                "channel": {"id": 2, "name": "Booking.com"},
                "guest-name": "Paolo Rossi",
                "created-at": "2026-07-01 10:00",
                "price": 285,
                "is-blocked-booking": False,
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=body)

    _install_transport(monkeypatch, handler)
    gw = SmoobuBookingGateway(api_key="k", apartment_id=APT_ID)
    res = asyncio.run(gw.bookings_changed_since(datetime(2026, 7, 13, 8, 30)))

    # Cancellations must be requested (Smoobu drops them by default) and the
    # filter is date-granular.
    assert seen["params"]["showCancellation"] == "true"
    assert seen["params"]["modifiedFrom"] == "2026-07-13"
    assert len(res) == 1
    r = res[0]
    assert r.status == "cancelled"  # type "cancellation" normalised to status
    assert r.booking_time == "2026-07-01 10:00"
    assert r.price == 285.0
