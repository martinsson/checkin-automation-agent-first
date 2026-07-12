"""
Concrete contract tests for DoorLockGateway implementations.

The real adapter (MakeDoorLockGateway) runs against a local HTTP server that
mimics Make's custom-webhook + webhook-response behaviour, so the full suite
runs without credentials or network access.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.adapters.make_door_lock import MakeDoorLockGateway
from src.adapters.simulator_door_lock import SimulatorDoorLockGateway
from src.ports.door_lock import DoorCodeRequest, DoorLockError
from tests.contracts.door_lock_gateway_contract import DoorLockGatewayContract


# -- fake Make webhook server ---------------------------------------------------


class _FakeMakeHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length)) if length else {}
        self.server.received.append({"headers": dict(self.headers), "payload": payload})

        status, body = self.server.next_response
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):  # silence request logging in test output
        pass


def _start_fake_make() -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), _FakeMakeHandler)
    server.received = []
    server.next_response = (200, json.dumps({"code": "43210987", "code_id": "pin-1", "name": "Alice"}))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.fixture
def fake_make():
    server = _start_fake_make()
    yield server
    server.shutdown()


def _request(**overrides) -> DoorCodeRequest:
    defaults = dict(
        person_name="Alice",
        starts_at="2026-07-15T13:00:00+02:00",
        ends_at="2026-07-18T15:00:00+02:00",
        purpose="early_checkin",
        reservation_id=42,
        code_name="Alice — resa 42",
    )
    defaults.update(overrides)
    return DoorCodeRequest(**defaults)


# -- contract subclasses ----------------------------------------------------------


class TestSimulatorDoorLockContract(DoorLockGatewayContract):
    def create_gateway(self) -> SimulatorDoorLockGateway:
        return SimulatorDoorLockGateway()


class TestMakeDoorLockGatewayContract(DoorLockGatewayContract):
    @pytest.fixture(autouse=True)
    def _server(self, fake_make):
        self._url = f"http://127.0.0.1:{fake_make.server_port}"
        yield

    def create_gateway(self) -> MakeDoorLockGateway:
        return MakeDoorLockGateway(webhook_url=self._url)


# -- simulator-specific tests -----------------------------------------------------


@pytest.mark.asyncio
async def test_simulator_records_requests():
    gateway = SimulatorDoorLockGateway()
    await gateway.create_code(_request(reservation_id=7))
    assert len(gateway.created) == 1
    assert gateway.created[0].reservation_id == 7


@pytest.mark.asyncio
async def test_simulator_fail_with_raises():
    gateway = SimulatorDoorLockGateway()
    gateway.fail_with = "lock offline"
    with pytest.raises(DoorLockError, match="lock offline"):
        await gateway.create_code(_request())


# -- Make adapter-specific tests --------------------------------------------------


@pytest.mark.asyncio
async def test_make_gateway_sends_expected_payload_and_api_key(fake_make):
    gateway = MakeDoorLockGateway(
        webhook_url=f"http://127.0.0.1:{fake_make.server_port}", api_key="secret-key"
    )
    await gateway.create_code(_request())

    received = fake_make.received[0]
    assert received["headers"].get("x-make-apikey") == "secret-key"
    assert received["payload"]["action"] == "create_door_code"
    assert received["payload"]["purpose"] == "early_checkin"
    assert received["payload"]["person_name"] == "Alice"
    assert received["payload"]["reservation_id"] == 42
    assert received["payload"]["starts_at"] == "2026-07-15T13:00:00+02:00"
    assert received["payload"]["ends_at"] == "2026-07-18T15:00:00+02:00"


@pytest.mark.asyncio
async def test_make_gateway_raises_on_http_error(fake_make):
    fake_make.next_response = (500, json.dumps({"error": "scenario failed"}))
    gateway = MakeDoorLockGateway(webhook_url=f"http://127.0.0.1:{fake_make.server_port}")
    with pytest.raises(DoorLockError, match="HTTP 500"):
        await gateway.create_code(_request())


@pytest.mark.asyncio
async def test_make_gateway_raises_when_code_missing(fake_make):
    fake_make.next_response = (200, json.dumps({"status": "ok"}))
    gateway = MakeDoorLockGateway(webhook_url=f"http://127.0.0.1:{fake_make.server_port}")
    with pytest.raises(DoorLockError, match="no 'code'"):
        await gateway.create_code(_request())


@pytest.mark.asyncio
async def test_make_gateway_raises_on_non_json_body(fake_make):
    fake_make.next_response = (200, "Accepted")  # Make's default reply without a response module
    gateway = MakeDoorLockGateway(webhook_url=f"http://127.0.0.1:{fake_make.server_port}")
    with pytest.raises(DoorLockError, match="non-JSON"):
        await gateway.create_code(_request())


# -- LIVE contract: the real adapter against the real Make webhook -----------
#
# This is the test that would have caught the deployed bug: when the Make
# scenario is queued/broken it replies with plain "Accepted", and
# MakeDoorLockGateway raises DoorLockError("non-JSON body") — so
# test_create_code_returns_non_empty_code (inherited from the contract) FAILS.
#
# It makes REAL calls (creates short, near-future Igloohome AlgoPINs on the
# test device) so it is opt-in: it SKIPS unless both env vars are set. That
# keeps it safe by default in CI. To run it — locally or in a dedicated/
# scheduled CI job with the secrets provided:
#
#   MAKE_IGLOOHOME_WEBHOOK_URL=... \
#   MAKE_IGLOOHOME_TEST_DEVICE_ID=<a real Igloohome device id> \
#   pytest tests/test_door_lock_gateway.py -k Live
#
# AlgoPINs are algorithmic and self-expire, so the codes it mints need no
# cleanup on the lock.

_LIVE_URL = os.environ.get("MAKE_IGLOOHOME_WEBHOOK_URL", "").strip()
_LIVE_DEVICE = os.environ.get("MAKE_IGLOOHOME_TEST_DEVICE_ID", "").strip()


@pytest.mark.skipif(
    not (_LIVE_URL and _LIVE_DEVICE),
    reason="live Make integration: set MAKE_IGLOOHOME_WEBHOOK_URL and "
    "MAKE_IGLOOHOME_TEST_DEVICE_ID (makes real calls that mint Igloohome codes)",
)
class TestMakeDoorLockLiveContract(DoorLockGatewayContract):
    def create_gateway(self) -> MakeDoorLockGateway:
        return MakeDoorLockGateway(
            webhook_url=_LIVE_URL,
            api_key=os.environ.get("MAKE_IGLOOHOME_API_KEY", "").strip(),
        )
