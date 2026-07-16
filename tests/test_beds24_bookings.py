"""
Beds24BookingGateway.stays_overlapping tests — the query the occupancy view
needs: bookings + owner blocks touching a date window, driven by a mocked httpx
transport (no network).
"""

import asyncio
import functools
from datetime import date

import httpx

from src.adapters.beds24_bookings import Beds24BookingGateway

_BODY = {
    "data": [
        {
            "id": 501,
            "propertyId": 328510,
            "firstName": "Long",
            "lastName": "Stay",
            "arrival": "2026-07-10",
            "departure": "2026-07-25",
            "status": "confirmed",
        },
        {
            # Owner availability block — Beds24 stores these as status "black".
            "id": 502,
            "propertyId": 328510,
            "arrival": "2026-07-20",
            "departure": "2026-07-22",
            "status": "black",
        },
        {
            # Cancelled — must be dropped even though the window matches.
            "id": 503,
            "propertyId": 328510,
            "firstName": "Gone",
            "arrival": "2026-07-18",
            "departure": "2026-07-19",
            "status": "cancelled",
        },
    ]
}


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "AsyncClient", functools.partial(httpx.AsyncClient, transport=transport)
    )


def test_stays_overlapping_queries_window_and_keeps_bookings_and_blocks(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/bookings"
        assert request.headers["token"] == "read-tok"
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=_BODY)

    _install_transport(monkeypatch, handler)
    gw = Beds24BookingGateway(read_token="read-tok")
    res = asyncio.run(gw.stays_overlapping(date(2026, 7, 16), date(2026, 7, 26)))

    # arrivalTo = last night of the window; departureFrom = window start.
    assert seen["params"]["arrivalTo"] == "2026-07-25"
    assert seen["params"]["departureFrom"] == "2026-07-16"

    ids = sorted(r.booking_id for r in res)
    assert ids == [501, 502]  # guest + block kept, cancelled dropped
    block = next(r for r in res if r.booking_id == 502)
    assert block.status == "black" and block.guest_name == "Blocked"
