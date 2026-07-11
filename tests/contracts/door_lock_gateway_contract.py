"""Contract tests for any DoorLockGateway implementation."""

from abc import ABC, abstractmethod

import pytest

from src.ports.door_lock import DoorCodeRequest, DoorLockGateway


def _request(**overrides) -> DoorCodeRequest:
    defaults = dict(
        reservation_id=42,
        guest_name="Alice",
        starts_at="2026-07-15T13:00:00+02:00",
        ends_at="2026-07-18T15:00:00+02:00",
        code_name="Alice — resa 42",
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
