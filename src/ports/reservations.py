"""
GuestBookingGateway port — read upcoming reservations and send a guest a
message on their booking (e.g. an ad-hoc early-arrival access code).

Backed in production by Beds24 (src/adapters/beds24_bookings.py).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

# Booking sources. A source is both a Reservation.source value AND the key under
# which the matching gateway is registered (see src/web/early_checkin.py), so a
# guest message routes back to the PMS the booking came from. Keep these two uses
# in lockstep — a mismatch silently sends to the wrong (or no) backend.
SOURCE_BEDS24 = "beds24"
SOURCE_SMOOBU = "smoobu"


@dataclass
class Reservation:
    """An upcoming stay, enough to fill the early-checkin form and message the guest."""
    booking_id: int
    property_id: int
    guest_name: str
    arrival: str      # ISO date "YYYY-MM-DD"
    departure: str    # ISO date "YYYY-MM-DD"
    channel: str = "" # e.g. "airbnb", "booking", "direct" — for display
    status: str = ""
    language: str = ""  # guest's preferred language code, e.g. "fr" / "en"
    source: str = SOURCE_BEDS24  # which PMS owns this booking — routes the message send


class BookingGatewayError(Exception):
    """Raised when the booking provider fails to read reservations or send a message."""


class GuestBookingGateway(ABC):
    """Port: read upcoming reservations and message a guest on their booking."""

    @abstractmethod
    async def upcoming_arrivals(self, days: int) -> list[Reservation]:
        """Reservations arriving from today through today+days (live bookings only)."""
        ...

    async def stays_overlapping(self, start: date, end: date) -> list[Reservation]:
        """
        Live stays occupying at least one night in the half-open window
        [start, end) — i.e. any booking with arrival < end and departure > start,
        **including stays that began before `start` and are still in progress**
        (which upcoming_arrivals, filtering by arrival date, would miss).

        Owner-side blocks (Beds24 `black`, Smoobu blocked bookings) are included:
        a blocked night is not available, so it must not read as free. Callers
        compute per-night occupancy themselves (arrival ≤ night < departure), so a
        provider may return slightly wider than the window. This powers the
        occupancy / free-night view. Raises BookingGatewayError on failure.

        Optional: the default raises NotImplementedError so a gateway that only
        supports arrival listing (e.g. a test fake) need not implement it.
        """
        raise NotImplementedError

    @abstractmethod
    async def send_guest_message(self, booking_id: int, message: str) -> None:
        """
        Send a literal message to the guest on a booking. The text is sent
        verbatim (no placeholder resolution). Raises BookingGatewayError on failure.
        """
        ...

    def managed_properties(self) -> list[tuple[str, int]]:
        """(display name, property id) pairs this gateway contributes to the
        property dropdown, for units not in the Beds24 YAML map. The id must match
        the `property_id` on this gateway's reservations. Empty by default."""
        return []
