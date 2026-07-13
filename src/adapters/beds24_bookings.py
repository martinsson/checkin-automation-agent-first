"""
Beds24-backed GuestBookingGateway.

Reads upcoming reservations with the long-life read token; sends guest messages
with a write:bookings access token minted on demand from the refresh token
(cached for its lifetime). See CLAUDE.md → "Beds24 API access" and the
beds24-messaging skill for the auth model and the /bookings/messages contract.
"""

import logging
import time
from datetime import date, timedelta

import httpx

from src.ports.reservations import (
    BookingGatewayError,
    GuestBookingGateway,
    Reservation,
)

log = logging.getLogger(__name__)

_BASE = "https://api.beds24.com/v2"
# Bookings that actually hold the room (exclude inquiries / cancellations).
_LIVE_STATUSES = {"confirmed", "new", "request"}


class Beds24BookingGateway(GuestBookingGateway):
    def __init__(
        self,
        read_token: str,
        refresh_token: str = "",
        timeout: float = 30.0,
    ):
        self._read_token = read_token
        self._refresh_token = refresh_token
        self._timeout = timeout
        self._write_token = ""
        self._write_token_expiry = 0.0  # monotonic seconds

    async def upcoming_arrivals(self, days: int) -> list[Reservation]:
        today = date.today()
        params = {
            "arrivalFrom": today.isoformat(),
            "arrivalTo": (today + timedelta(days=days)).isoformat(),
            "status": list(_LIVE_STATUSES),
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{_BASE}/bookings",
                    params=params,
                    headers={"token": self._read_token, "accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise BookingGatewayError(f"Beds24 bookings request failed: {exc}") from exc

        if resp.status_code != 200:
            raise BookingGatewayError(
                f"Beds24 bookings returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json().get("data", []) or []

        out: list[Reservation] = []
        for b in data:
            if b.get("status") not in _LIVE_STATUSES:
                continue
            name = f"{b.get('firstName', '') or ''} {b.get('lastName', '') or ''}".strip()
            out.append(
                Reservation(
                    booking_id=int(b["id"]),
                    property_id=int(b.get("propertyId", 0) or 0),
                    guest_name=name,
                    arrival=b.get("arrival", "") or "",
                    departure=b.get("departure", "") or "",
                    channel=(b.get("channel") or b.get("referer") or "").strip(),
                    status=b.get("status", "") or "",
                    language=(b.get("lang") or "").strip().lower(),
                )
            )
        out.sort(key=lambda r: r.arrival)
        return out

    async def send_guest_message(self, booking_id: int, message: str) -> None:
        token = await self._get_write_token()
        body = [{"bookingId": int(booking_id), "message": message}]
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{_BASE}/bookings/messages",
                    json=body,
                    headers={"token": token, "content-type": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise BookingGatewayError(f"Beds24 message request failed: {exc}") from exc

        # Beds24 answers a successful message POST with HTTP 201 (created).
        if resp.status_code not in (200, 201):
            raise BookingGatewayError(
                f"Beds24 message returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        # The endpoint answers with a per-item array; surface any item failure.
        try:
            payload = resp.json()
        except ValueError as exc:
            raise BookingGatewayError(
                f"Beds24 message returned non-JSON body: {resp.text[:200]}"
            ) from exc
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict) and item.get("success") is False:
                raise BookingGatewayError(f"Beds24 rejected the message: {item}")
        log.info("Guest message sent on booking %s (%d chars)", booking_id, len(message))

    async def _get_write_token(self) -> str:
        """Mint (and cache) a write:bookings access token from the refresh token."""
        if not self._refresh_token:
            raise BookingGatewayError(
                "No BEDS24_REFRESH_TOKEN configured — cannot send guest messages."
            )
        if self._write_token and time.monotonic() < self._write_token_expiry:
            return self._write_token
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{_BASE}/authentication/token",
                    headers={"refreshToken": self._refresh_token},
                )
        except httpx.HTTPError as exc:
            raise BookingGatewayError(f"Beds24 token mint failed: {exc}") from exc
        if resp.status_code != 200:
            raise BookingGatewayError(
                f"Beds24 token mint returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json()
        token = str(data.get("token", "")).strip()
        if not token:
            raise BookingGatewayError(f"Beds24 token mint response has no token: {data}")
        # Refresh a bit before the stated expiry to avoid using a just-expired token.
        expires_in = int(data.get("expiresIn", 86400) or 86400)
        self._write_token = token
        self._write_token_expiry = time.monotonic() + max(60, expires_in - 300)
        return token
