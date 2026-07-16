"""
Beds24BookingGateway adapter tests — the /bookings query and parsing, driven by
a mocked httpx transport (no network).

Focus: the dropdown must include a guest who is already in-house (arrived earlier,
checking out today), so the query selects stays *overlapping* the window
(`departureFrom = today`) rather than only those arriving from today on.
"""

import asyncio
import functools
from datetime import date

import httpx

from src.adapters.beds24_bookings import Beds24BookingGateway

_BOOKINGS_BODY = {
    "data": [
        {
            "id": 501,
            "propertyId": 328510,
            "firstName": "Marine",
            "lastName": "Cuenot",
            "arrival": date.today().isoformat(),
            "departure": "2026-12-01",
            "status": "confirmed",
            "referer": "airbnb",
            "lang": "FR",
        },
        {
            # In-house guest: arrived days ago, checks out today.
            "id": 502,
            "propertyId": 328510,
            "firstName": "Paul",
            "lastName": "Durand",
            "arrival": "2026-07-12",
            "departure": date.today().isoformat(),
            "status": "new",
            "referer": "direct",
            "lang": "en",
        },
    ]
}


def _install_transport(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "AsyncClient", functools.partial(httpx.AsyncClient, transport=transport)
    )


def test_upcoming_arrivals_queries_by_departure_and_keeps_in_house_guest(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/bookings"
        assert request.headers["token"] == "read-tok"
        # Query by departure (still-ongoing stays), not by arrival, so a guest
        # who arrived earlier and checks out today stays in the list.
        assert request.url.params["departureFrom"] == date.today().isoformat()
        assert "arrivalFrom" not in request.url.params
        assert "arrivalTo" in request.url.params
        return httpx.Response(200, json=_BOOKINGS_BODY)

    _install_transport(monkeypatch, handler)
    gw = Beds24BookingGateway(read_token="read-tok")
    res = asyncio.run(gw.upcoming_arrivals(60))

    ids = {r.booking_id for r in res}
    assert ids == {501, 502}  # includes the checkout-today guest
    in_house = next(r for r in res if r.booking_id == 502)
    assert in_house.guest_name == "Paul Durand"
    assert in_house.departure == date.today().isoformat()
    assert in_house.source == "beds24"
