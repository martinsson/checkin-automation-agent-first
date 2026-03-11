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
