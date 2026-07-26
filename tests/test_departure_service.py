"""
DepartureNotificationService — enrich → summarise → route → send, with the
graceful-degradation paths (Beds24 fails, LLM fails, unmapped property).

Real SqliteRequestMemory (in-memory) for dedup + event log; everything else stubbed.
"""

from src.adapters.sqlite_memory import SqliteRequestMemory
from src.config.cleaner_map import CleanerContact, CleanerMap
from src.departure.service import DepartureNotificationService, DeparturePayload
from src.departure.summarizer import DepartureSummary
from src.ports.cleaner import CleanerNotifier, DepartureNotice
from src.ports.reservations import BookingGatewayError, Reservation


# -- stubs -------------------------------------------------------------------

class _StubNotifier(CleanerNotifier):
    def __init__(self):
        self.sent: list[tuple[str, DepartureNotice]] = []

    async def send_query(self, query):  # unused here
        return "n/a"

    async def poll_responses(self):
        return []

    async def send_departure_notice(self, to_email: str, notice: DepartureNotice) -> str:
        self.sent.append((to_email, notice))
        return "track-123"


class _FailNotifier(_StubNotifier):
    async def send_departure_notice(self, to_email, notice):
        raise RuntimeError("smtp down")


class _StubSummarizer:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def summarize(self, ctx):
        self.calls.append(ctx)
        return self._result


class _StubGateway:
    def __init__(self, booking=None, error=None):
        self._booking = booking
        self._error = error
        self.calls = []

    async def get_booking(self, booking_id):
        self.calls.append(booking_id)
        if self._error:
            raise self._error
        return self._booking


def _booking(**kw):
    base = dict(
        booking_id=42, property_id=328510, guest_name="Alice",
        arrival="2026-07-28", departure="2026-08-01", phone="+33600000000",
    )
    base.update(kw)
    return Reservation(**base)


def _summary(**kw):
    base = dict(
        departure_status="leaving_early", certainty="estimated",
        summary_fr="Le voyageur prévoit de partir vers 9h.", estimated_time="09:00",
    )
    base.update(kw)
    return DepartureSummary(**base)


def _map():
    return CleanerMap(
        contacts={328510: CleanerContact(name="V-Clean", email="vclean@example.com")},
        default_email="fallback@example.com",
    )


def _payload(**kw):
    base = dict(
        action_item_id="ai-1", booking_id=42, category="early_departure",
        guest_name="Alice", property_name="Le Fernand",
        message_summary="Guest will leave around 9am.",
    )
    base.update(kw)
    return DeparturePayload(**base)


def _service(notifier, summarizer, gateway, cleaner_map=None, memory=None):
    return DepartureNotificationService(
        memory=memory or SqliteRequestMemory(":memory:"),
        notifier=notifier,
        cleaner_map=cleaner_map or _map(),
        summarizer=summarizer,
        booking_gateway=gateway,
    )


# -- tests -------------------------------------------------------------------

async def test_happy_path_sends_to_mapped_cleaner_with_phone():
    notifier = _StubNotifier()
    svc = _service(notifier, _StubSummarizer(_summary()), _StubGateway(_booking()))

    result = await svc.handle(_payload())

    assert result["status"] == "sent"
    assert len(notifier.sent) == 1
    to_email, notice = notifier.sent[0]
    assert to_email == "vclean@example.com"          # routed by propertyId
    assert notice.guest_phone == "+33600000000"      # phone from Beds24
    assert notice.certainty == "estimated"
    assert "9h" in notice.summary_fr


async def test_beds24_failure_degrades_no_phone_default_cleaner():
    notifier = _StubNotifier()
    gateway = _StubGateway(error=BookingGatewayError("beds24 down"))
    svc = _service(notifier, _StubSummarizer(_summary()), gateway)

    result = await svc.handle(_payload())

    assert result["status"] == "sent"
    to_email, notice = notifier.sent[0]
    assert to_email == "fallback@example.com"        # no propertyId → default
    assert notice.guest_phone == ""                  # degraded: no phone


async def test_llm_failure_uses_templated_fallback():
    notifier = _StubNotifier()
    svc = _service(notifier, _StubSummarizer(None), _StubGateway(_booking()))

    result = await svc.handle(_payload())

    assert result["status"] == "sent"
    _, notice = notifier.sent[0]
    # Fallback template still carries HostBuddy's message summary verbatim.
    assert "Guest will leave around 9am." in notice.summary_fr
    assert notice.departure_status == "leaving_early"


async def test_unmapped_property_uses_default_cleaner():
    notifier = _StubNotifier()
    gateway = _StubGateway(_booking(property_id=111111))  # not in the map
    svc = _service(notifier, _StubSummarizer(_summary()), gateway)

    await svc.handle(_payload())
    to_email, _ = notifier.sent[0]
    assert to_email == "fallback@example.com"


async def test_dedup_second_delivery_same_booking_and_category():
    notifier = _StubNotifier()
    memory = SqliteRequestMemory(":memory:")
    svc = _service(notifier, _StubSummarizer(_summary()), _StubGateway(_booking()), memory=memory)

    first = await svc.handle(_payload(action_item_id="ai-1"))
    second = await svc.handle(_payload(action_item_id="ai-2"))  # different delivery, same booking+cat

    assert first["status"] == "sent"
    assert second["status"] == "duplicate"
    assert len(notifier.sent) == 1                   # not sent twice


async def test_send_failure_does_not_mark_dedup():
    notifier = _FailNotifier()
    memory = SqliteRequestMemory(":memory:")
    svc = _service(notifier, _StubSummarizer(_summary()), _StubGateway(_booking()), memory=memory)

    result = await svc.handle(_payload())
    assert result["status"] == "error"
    # A later re-delivery should NOT be treated as a duplicate (retry allowed).
    assert await memory.has_action_item_been_seen("dep:42:early_departure") is False


async def test_event_logged_on_success():
    notifier = _StubNotifier()
    memory = SqliteRequestMemory(":memory:")
    svc = _service(notifier, _StubSummarizer(_summary()), _StubGateway(_booking()), memory=memory)

    await svc.handle(_payload())
    events = await memory.get_events(42)
    assert len(events) == 1
    assert events[0].event_type == "departure_notified"
    assert events[0].payload["cleaner_email"] == "vclean@example.com"
    assert events[0].payload["phone_included"] is True
