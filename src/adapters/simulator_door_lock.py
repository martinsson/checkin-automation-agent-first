"""
In-memory DoorLockGateway simulator for tests and local runs.
"""

from src.ports.door_lock import DoorCode, DoorCodeRequest, DoorLockError, DoorLockGateway


class SimulatorDoorLockGateway(DoorLockGateway):
    """
    Returns deterministic PIN codes and records every request.

    Set `fail_with` to make the next create_code call raise DoorLockError,
    to exercise error paths.
    """

    def __init__(self):
        self.created: list[DoorCodeRequest] = []
        self.fail_with: str | None = None

    async def create_code(self, request: DoorCodeRequest) -> DoorCode:
        if self.fail_with is not None:
            raise DoorLockError(self.fail_with)
        self.created.append(request)
        pin = f"{1000 + len(self.created):04d}{request.reservation_id % 10000:04d}"
        return DoorCode(
            code=pin,
            code_id=f"sim-{len(self.created)}",
            name=request.code_name or f"Guest {request.guest_name}",
        )
