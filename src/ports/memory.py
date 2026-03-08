"""
RequestMemory port — tracks requests, drafts, and owner reviews.

Every message the system wants to send (acknowledgment, cleaner query,
guest reply) is saved as a draft first.  The owner reviews it, marks it
OK or NOK, optionally writes what they actually sent, and leaves a comment.
Over time the NOK entries become training data for improving prompts.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class RequestStatus(str, Enum):
    pending_ack = "pending_ack"
    pending_cleaner = "pending_cleaner"
    pending_reply = "pending_reply"
    done = "done"


@dataclass
class ProcessedRequest:
    reservation_id: int
    intent: str          # "early_checkin" or "late_checkout"
    status: RequestStatus
    created_at: datetime
    request_id: str      # correlation ID
    guest_message: str   # the original guest message
    guest_name: str = ""
    property_name: str = ""
    original_time: str = ""   # default check-in or check-out time
    requested_time: str = ""  # time the guest requested
    relevant_date: str = ""   # arrival_date for early_checkin, departure_date for late_checkout


@dataclass
class Draft:
    draft_id: int
    request_id: str
    reservation_id: int
    intent: str
    step: str              # "acknowledgment", "cleaner_query", "guest_reply"
    draft_body: str        # what the AI proposed
    verdict: str           # "pending", "ok", "nok"
    actual_message_sent: str | None   # what the owner actually sent
    owner_comment: str | None         # why they changed it
    created_at: datetime
    reviewed_at: datetime | None
    sent_at: datetime | None = None


class RequestMemory(ABC):
    """
    Port: remember requests and drafts, support owner review workflow.

    A guest can make both an early-checkin AND a late-checkout request —
    those are tracked independently (different intent values).
    """

    # -- message-level dedup -------------------------------------------------

    @abstractmethod
    async def has_message_been_seen(self, message_id: int) -> bool:
        """True if this message_id was already classified in a previous cycle."""
        ...

    @abstractmethod
    async def mark_message_seen(self, message_id: int, reservation_id: int) -> None:
        """Record that this message_id has been classified."""
        ...

    # -- request tracking ----------------------------------------------------

    @abstractmethod
    async def has_been_processed(self, reservation_id: int, intent: str) -> bool:
        """True if this intent already has a request for this reservation."""
        ...

    @abstractmethod
    async def save_request(
        self,
        reservation_id: int,
        intent: str,
        request_id: str,
        guest_message: str,
        guest_name: str = "",
        property_name: str = "",
        original_time: str = "",
        requested_time: str = "",
        relevant_date: str = "",
    ) -> None:
        """Create a new request record."""
        ...

    @abstractmethod
    async def update_status(self, request_id: str, status: str) -> None:
        """Update the status of a request."""
        ...

    @abstractmethod
    async def get_request(self, request_id: str) -> ProcessedRequest | None:
        """Look up a request by its ID."""
        ...

    @abstractmethod
    async def get_history(self, reservation_id: int) -> list[ProcessedRequest]:
        """Return all requests for a reservation, oldest first."""
        ...

    # -- draft management ----------------------------------------------------

    @abstractmethod
    async def save_draft(
        self,
        request_id: str,
        reservation_id: int,
        intent: str,
        step: str,
        draft_body: str,
    ) -> int:
        """Save a draft for owner review. Returns the draft_id."""
        ...

    @abstractmethod
    async def get_pending_drafts(self) -> list[Draft]:
        """Return all drafts awaiting owner review, oldest first."""
        ...

    @abstractmethod
    async def get_draft(self, draft_id: int) -> Draft | None:
        """Look up a draft by its ID."""
        ...

    @abstractmethod
    async def review_draft(
        self,
        draft_id: int,
        verdict: str,
        actual_message_sent: str | None = None,
        owner_comment: str | None = None,
    ) -> None:
        """
        Record the owner's verdict on a draft.

        verdict: "ok" (send as-is) or "nok" (owner wrote something different)
        actual_message_sent: what the owner actually sent (if different from draft)
        owner_comment: why the owner changed it (learning data for prompts)
        """
        ...

    @abstractmethod
    async def get_drafts_for_request(self, request_id: str) -> list[Draft]:
        """Return all drafts for a given request_id, ordered by created_at."""
        ...

    @abstractmethod
    async def get_reviewed_unsent_drafts(self) -> list[Draft]:
        """Return drafts where verdict IN ('ok','nok') AND sent_at IS NULL, ordered by created_at."""
        ...

    @abstractmethod
    async def mark_draft_sent(self, draft_id: int) -> None:
        """Set sent_at to current UTC timestamp for the given draft."""
        ...

    # -- retry / compensation --------------------------------------------------

    @abstractmethod
    async def delete_request(self, request_id: str) -> None:
        """Delete a request and all its drafts. Used by retry script."""
        ...

    @abstractmethod
    async def delete_seen_message(self, message_id: int) -> None:
        """Remove the seen_messages entry so the message can be re-classified."""
        ...

    # -- agent event log -------------------------------------------------------

    @abstractmethod
    async def append_event(
        self,
        reservation_id: int,
        event_type: str,
        payload: dict,
    ) -> None:
        """Append an event to the agent event log for this reservation."""
        ...

    @abstractmethod
    async def get_events(self, reservation_id: int) -> list["AgentEvent"]:
        """Return all agent events for a reservation, oldest first."""
        ...


@dataclass
class AgentEvent:
    reservation_id: int
    event_type: str    # e.g. "hostbuddy_action_item", "cleaner_email_sent",
                       #      "cleaner_reply", "guest_draft_created", "wait"
    payload: dict
    created_at: datetime
