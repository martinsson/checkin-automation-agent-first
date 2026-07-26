"""
CleanerNotifier port — abstraction for communicating with the cleaning staff.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CleanerQuery:
    """A query sent to the cleaner asking about a guest request."""
    request_id: str
    cleaner_name: str
    guest_name: str
    property_name: str
    request_type: str       # "early_checkin" or "late_checkout"
    original_time: str      # default check-in or check-out time
    requested_time: str     # time the guest requested
    date: str               # ISO date string
    message: str            # free-form message body from Claude


@dataclass
class CleanerResponse:
    """A response from the cleaner to a query."""
    request_id: str
    raw_text: str           # the cleaner's reply verbatim


@dataclass
class DepartureNotice:
    """A one-way, fire-and-forget notice that a guest is leaving / has left.

    Unlike CleanerQuery there is no reply loop: the cleaner reads the context and
    decides for themselves whether to come earlier."""
    property_name: str
    guest_name: str
    departure_date: str          # ISO date the stay ends
    summary_fr: str              # French summary (from the LLM or a fallback template)
    departure_status: str = ""   # "already_left" | "leaving_early" | ""
    certainty: str = ""          # "confirmed" | "estimated" | ""
    estimated_time: str | None = None
    guest_phone: str = ""        # included when known; blank line omitted otherwise


class CleanerNotifier(ABC):
    """Port: send queries to the cleaner and poll for their replies."""

    @abstractmethod
    async def send_query(self, query: CleanerQuery) -> str:
        """
        Send a query to the cleaner.
        Returns a tracking ID (e.g. email Message-ID).
        """
        ...

    @abstractmethod
    async def poll_responses(self) -> list[CleanerResponse]:
        """
        Poll for replies from the cleaner since the last poll.
        Returns a list of responses, oldest first.
        """
        ...

    @abstractmethod
    async def send_departure_notice(self, to_email: str, notice: DepartureNotice) -> str:
        """
        Send a one-way departure notice to a specific cleaner address.
        The recipient is passed per call (it varies by property), unlike the
        query flow which is bound to a single configured address.
        Returns a tracking ID (e.g. email Message-ID).
        """
        ...
