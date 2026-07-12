"""Contract tests for any DoorLockGateway implementation.

Run against the in-memory simulator, the Make adapter over a fake HTTP server,
AND (opt-in) the Make adapter against the real webhook — see
tests/test_door_lock_gateway.py. Running the same spec against the real
integration is what catches a scenario that silently returns "Accepted"
instead of a JSON code.
"""

import os
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.ports.door_lock import DoorCodeRequest, DoorLockGateway

# A near-future, hour-aligned window in Europe/Paris, in the SAME naive format
# the app sends (no offset) — so a live call exercises the real Make date
# parsing and Igloohome's "no past window" rule. Recomputed per call so the
# window is always valid whenever the suite runs.
_TZ = ZoneInfo("Europe/Paris")


def _future_window() -> tuple[str, str]:
    now = datetime.now(_TZ).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    start = now + timedelta(hours=1)
    end = now + timedelta(hours=3)
    fmt = "%Y-%m-%dT%H:%M:00"
    return start.strftime(fmt), end.strftime(fmt)


def _request(**overrides) -> DoorCodeRequest:
    start, end = _future_window()
    defaults = dict(
        person_name="Alice",
        starts_at=start,
        ends_at=end,
        purpose="early_checkin",
        reservation_id=42,
        # Real device only matters for the live subclass; a dummy is fine for
        # the simulator / fake-server subclasses (they ignore it).
        device_id=os.environ.get("MAKE_IGLOOHOME_TEST_DEVICE_ID", "SIM-DEVICE"),
        code_name="pytest contract — ignore",
    )
    defaults.update(overrides)
    return DoorCodeRequest(**defaults)


class DoorLockGatewayContract(ABC):

    @abstractmethod
    def create_gateway(self) -> DoorLockGateway:
        ...

    @pytest.mark.asyncio
    async def test_create_code_returns_non_empty_code(self):
        gateway = self.create_gateway()
        door_code = await gateway.create_code(_request())
        assert door_code.code.strip() != ""

    @pytest.mark.asyncio
    async def test_create_code_returns_a_name(self):
        gateway = self.create_gateway()
        door_code = await gateway.create_code(_request(code_name="Bob — resa 7"))
        assert door_code.name.strip() != ""

    @pytest.mark.asyncio
    async def test_two_requests_both_yield_codes(self):
        gateway = self.create_gateway()
        first = await gateway.create_code(_request(reservation_id=1))
        second = await gateway.create_code(_request(reservation_id=2))
        assert first.code.strip() != ""
        assert second.code.strip() != ""
