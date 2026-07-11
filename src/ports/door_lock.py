"""
DoorLockGateway port — abstraction for creating temporary door access codes
(e.g. an Igloohome PIN) for a guest's stay.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class DoorCodeRequest:
    """A request to create a temporary access code for a stay."""
    reservation_id: int
    guest_name: str
    starts_at: str          # ISO 8601 datetime — code becomes valid
    ends_at: str            # ISO 8601 datetime — code expires
    code_name: str = ""     # label shown in the lock app (e.g. "Alice — resa 42")


@dataclass
class DoorCode:
    """A created access code."""
    code: str               # the PIN the guest types on the keypad
    code_id: str = ""       # provider-side identifier (for later revocation)
    name: str = ""          # label the code was registered under


class DoorLockError(Exception):
    """Raised when the door lock provider fails to create a code."""


class DoorLockGateway(ABC):
    """Port: create temporary door access codes."""

    @abstractmethod
    async def create_code(self, request: DoorCodeRequest) -> DoorCode:
        """
        Create a temporary access code valid for the requested window.
        Raises DoorLockError on provider failure.
        """
        ...
