"""
GuestBookingGateway port — read upcoming reservations and send a guest a
message on their booking (e.g. an ad-hoc early-arrival access code).

Backed in production by Beds24 (src/adapters/beds24_bookings.py).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


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


class BookingGatewayError(Exception):
    """Raised when the booking provider fails to read reservations or send a message."""


class GuestBookingGateway(ABC):
    """Port: read upcoming reservations and message a guest on their booking."""

    @abstractmethod
    async def upcoming_arrivals(self, days: int) -> list[Reservation]:
        """Reservations arriving from today through today+days (live bookings only)."""
        ...

    @abstractmethod
    async def send_guest_message(self, booking_id: int, message: str) -> None:
        """
        Send a literal message to the guest on a booking. The text is sent
        verbatim (no placeholder resolution). Raises BookingGatewayError on failure.
        """
        ...
