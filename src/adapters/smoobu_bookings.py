"""
Smoobu-backed GuestBookingGateway.

Lets the /early-checkin form list and message L'Hippocrate guests: Hippocrate is
managed in **Smoobu**, not Beds24 (see config/beds24_properties.yaml), so its
bookings never show up through the Beds24 gateway.

Auth is a single long-life API key sent as the Smoobu `Api-Key` header. Smoobu's
WAF 403s the default httpx/urllib User-Agent, so a browser-like UA is sent — the
same trick scripts/access_text_fetch.py already relies on.
"""

import logging
from datetime import date, timedelta

import httpx

from src.ports.reservations import (
    SOURCE_SMOOBU,
    BookingGatewayError,
    GuestBookingGateway,
    Reservation,
)

log = logging.getLogger(__name__)

_BASE = "https://login.smoobu.com/api"
# Smoobu's WAF blocks the default httpx User-Agent; send a normal-looking one.
_UA = "Mozilla/5.0 (checkin-automation)"


class SmoobuBookingGateway(GuestBookingGateway):
    def __init__(
        self,
        api_key: str,
        apartment_id: int,
        apartment_name: str = "Hippocrate",
        timeout: float = 30.0,
    ):
        self._api_key = api_key
        self._apartment_id = int(apartment_id)
        self._apartment_name = apartment_name
        self._timeout = timeout

    def managed_properties(self) -> list[tuple[str, int]]:
        """(display name, property id) pairs to add to the property dropdown. The
        id is the Smoobu apartment id — it must match the `property_id` on the
        reservations below so the form's client-side filter groups them together."""
        return [(self._apartment_name, self._apartment_id)]

    def _headers(self) -> dict:
        return {
            "Api-Key": self._api_key,
            "User-Agent": _UA,
            "accept": "application/json",
        }

    async def upcoming_arrivals(self, days: int) -> list[Reservation]:
        today = date.today()
        params = {
            "apartmentId": self._apartment_id,
            "arrivalFrom": today.isoformat(),
            "arrivalTo": (today + timedelta(days=days)).isoformat(),
            "showCancellation": "false",
            "pageSize": 100,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{_BASE}/reservations", params=params, headers=self._headers()
                )
        except httpx.HTTPError as exc:
            raise BookingGatewayError(f"Smoobu reservations request failed: {exc}") from exc
        if resp.status_code != 200:
            raise BookingGatewayError(
                f"Smoobu reservations returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        bookings = resp.json().get("bookings", []) or []

        out: list[Reservation] = []
        for b in bookings:
            # Owner-side calendar blocks aren't guests to message.
            if b.get("is-blocked-booking") or b.get("type") == "block":
                continue
            arrival = (b.get("arrival") or "").strip()
            name = (b.get("guest-name") or "").strip() or (
                f"{b.get('firstname', '') or ''} {b.get('lastname', '') or ''}".strip()
            )
            channel = b.get("channel")
            channel_name = channel.get("name") if isinstance(channel, dict) else (channel or "")
            out.append(
                Reservation(
                    booking_id=int(b["id"]),
                    property_id=self._apartment_id,
                    guest_name=name,
                    arrival=arrival,
                    departure=(b.get("departure") or "").strip(),
                    channel=str(channel_name or "").strip(),
                    status=str(b.get("status") or "").strip(),
                    language=str(b.get("language") or "").strip().lower(),
                    source=SOURCE_SMOOBU,
                )
            )
        out.sort(key=lambda r: r.arrival)
        return out

    async def send_guest_message(self, booking_id: int, message: str) -> None:
        body = {"messageBody": message}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{_BASE}/reservations/{int(booking_id)}/messages/send-message-to-guest",
                    json=body,
                    headers={**self._headers(), "content-type": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise BookingGatewayError(f"Smoobu message request failed: {exc}") from exc
        # Smoobu answers a successful send-message-to-guest with HTTP 201 (created).
        if resp.status_code not in (200, 201):
            raise BookingGatewayError(
                f"Smoobu message returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        log.info("Guest message sent on Smoobu booking %s (%d chars)", booking_id, len(message))
