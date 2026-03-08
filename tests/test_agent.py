"""
Agent unit tests — stub the Anthropic client so no real API calls are made.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from src.adapters.sqlite_memory import SqliteRequestMemory
from src.agent.agent import AgentRunner
from src.ports.cleaner import CleanerNotifier, CleanerQuery, CleanerResponse


class _StubNotifier(CleanerNotifier):
    def __init__(self):
        self.sent: list[CleanerQuery] = []

    async def send_query(self, query: CleanerQuery) -> str:
        self.sent.append(query)
        return "msg-id-stub"

    async def poll_responses(self) -> list[CleanerResponse]:
        return []


def _make_tool_response(tool_name: str, tool_input: dict):
    """Build a fake Anthropic response with one tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input

    response = MagicMock()
    response.content = [block]
    return response


@pytest.fixture
def memory():
    return SqliteRequestMemory(":memory:")


@pytest.fixture
def notifier():
    return _StubNotifier()


@pytest.fixture
def agent(memory, notifier):
    runner = AgentRunner(memory=memory, cleaner_notifier=notifier, anthropic_api_key="test")
    return runner


@pytest.mark.asyncio
async def test_first_event_triggers_cleaner_email(agent, memory, notifier):
    """On first hostbuddy event, Claude should call send_cleaner_email."""
    await memory.save_request(42, "early_checkin", "req-1", "Can I check in at 11?",
                               guest_name="Alice", property_name="La Maison")

    fake_response = _make_tool_response(
        "send_cleaner_email",
        {"date": "2026-03-15", "message": "Guest Alice wants early check-in at 11am on 2026-03-15"},
    )

    with patch.object(agent._client.messages, "create", return_value=fake_response):
        await agent.run(
            reservation_id=42,
            event_type="hostbuddy_action_item",
            event_payload={"category": "early_checkin", "guest_name": "Alice"},
            request_id="req-1",
            intent="early_checkin",
            guest_name="Alice",
            property_name="La Maison",
        )

    # Cleaner email was sent
    assert len(notifier.sent) == 1
    assert notifier.sent[0].date == "2026-03-15"
    assert notifier.sent[0].request_id == "req-1"

    # Events: hostbuddy_action_item + cleaner_email_sent
    events = await memory.get_events(42)
    assert len(events) == 2
    assert events[0].event_type == "hostbuddy_action_item"
    assert events[1].event_type == "cleaner_email_sent"
    assert events[1].payload["date"] == "2026-03-15"


@pytest.mark.asyncio
async def test_cleaner_reply_triggers_guest_draft(agent, memory, notifier):
    """After cleaner replies, Claude should call create_guest_draft."""
    await memory.save_request(42, "early_checkin", "req-1", "msg",
                               guest_name="Alice", property_name="La Maison")
    # Seed prior events so Claude sees full history
    await memory.append_event(42, "hostbuddy_action_item", {"category": "early_checkin"})
    await memory.append_event(42, "cleaner_email_sent", {"date": "2026-03-15"})

    fake_response = _make_tool_response(
        "create_guest_draft",
        {"body": "Great news! Early check-in at 11am is possible on March 15."},
    )

    with patch.object(agent._client.messages, "create", return_value=fake_response):
        await agent.run(
            reservation_id=42,
            event_type="cleaner_reply",
            event_payload={"raw_text": "Oui c'est possible à 11h"},
            request_id="req-1",
            intent="early_checkin",
        )

    # Guest draft was saved
    drafts = await memory.get_pending_drafts()
    assert len(drafts) == 1
    assert "11am" in drafts[0].draft_body

    # Events: prior 2 + cleaner_reply + guest_draft_created
    events = await memory.get_events(42)
    assert events[-2].event_type == "cleaner_reply"
    assert events[-1].event_type == "guest_draft_created"


@pytest.mark.asyncio
async def test_wait_appends_wait_event(agent, memory, notifier):
    """When Claude calls wait, a wait event is appended and no email/draft is created."""
    await memory.save_request(42, "early_checkin", "req-1", "msg")
    await memory.append_event(42, "hostbuddy_action_item", {})
    await memory.append_event(42, "cleaner_email_sent", {})

    fake_response = _make_tool_response("wait", {"reason": "still waiting for cleaner"})

    with patch.object(agent._client.messages, "create", return_value=fake_response):
        await agent.run(
            reservation_id=42,
            event_type="cleaner_email_sent",
            event_payload={},
            request_id="req-1",
            intent="early_checkin",
        )

    assert len(notifier.sent) == 0
    assert len(await memory.get_pending_drafts()) == 0

    events = await memory.get_events(42)
    wait_events = [e for e in events if e.event_type == "wait"]
    assert len(wait_events) == 1
    assert wait_events[0].payload["reason"] == "still waiting for cleaner"


@pytest.mark.asyncio
async def test_no_tool_call_appends_wait_event(agent, memory, notifier):
    """If Claude returns no tool_use block, a fallback wait event is appended."""
    await memory.save_request(42, "early_checkin", "req-1", "msg")

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "I'm not sure what to do."
    fake_response = MagicMock()
    fake_response.content = [text_block]

    with patch.object(agent._client.messages, "create", return_value=fake_response):
        await agent.run(
            reservation_id=42,
            event_type="hostbuddy_action_item",
            event_payload={},
            request_id="req-1",
            intent="early_checkin",
        )

    events = await memory.get_events(42)
    assert any(e.event_type == "wait" for e in events)
