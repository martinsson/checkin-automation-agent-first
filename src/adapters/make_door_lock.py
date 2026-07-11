"""
Make.com-backed DoorLockGateway implementation.

Calls a Make custom webhook whose scenario creates an Igloohome PIN code
and returns it in the webhook response. See docs/make/README.md for the
scenario setup and the request/response contract.
"""

import logging

import httpx

from src.ports.door_lock import DoorCode, DoorCodeRequest, DoorLockError, DoorLockGateway

log = logging.getLogger(__name__)


class MakeDoorLockGateway(DoorLockGateway):
    """
    POSTs the code request to a Make webhook (MAKE_IGLOOHOME_WEBHOOK_URL).

    The Make scenario is expected to answer with a JSON body containing at
    least {"code": "<pin>"}; "code_id" and "name" are optional extras.
    """

    def __init__(self, webhook_url: str, api_key: str = "", timeout: float = 30.0):
        self._webhook_url = webhook_url
        self._api_key = api_key
        self._timeout = timeout

    async def create_code(self, request: DoorCodeRequest) -> DoorCode:
        payload = {
            "action": "create_door_code",
            "purpose": request.purpose,
            "reservation_id": request.reservation_id,
            "person_name": request.person_name,
            "property": request.property_name,
            "starts_at": request.starts_at,
            "ends_at": request.ends_at,
            "code_name": request.code_name,
        }
        headers = {}
        if self._api_key:
            # Matches Make's "API key" webhook authentication setting
            headers["x-make-apikey"] = self._api_key

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._webhook_url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise DoorLockError(f"Make webhook request failed: {exc}") from exc

        if response.status_code != 200:
            raise DoorLockError(
                f"Make webhook returned HTTP {response.status_code}: {response.text[:200]}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise DoorLockError(
                f"Make webhook returned non-JSON body: {response.text[:200]}"
            ) from exc

        code = str(data.get("code", "")).strip()
        if not code:
            raise DoorLockError(f"Make webhook response has no 'code' field: {data}")

        door_code = DoorCode(
            code=code,
            code_id=str(data.get("code_id", "")),
            name=str(data.get("name", "")) or request.code_name,
        )
        log.info(
            "Igloohome code created via Make: reservation=%s code_id=%s window=%s→%s",
            request.reservation_id, door_code.code_id, request.starts_at, request.ends_at,
        )
        return door_code
