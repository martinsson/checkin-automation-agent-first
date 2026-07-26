"""
Departure summarizer — structured French summary via one forced tool call.
Anthropic client is stubbed; no network.
"""

from unittest.mock import MagicMock

from src.departure.summarizer import DepartureContext, DepartureSummarizer


def _tool_response(tool_input: dict):
    block = MagicMock()
    block.type = "tool_use"
    block.input = tool_input
    resp = MagicMock()
    resp.content = [block]
    return resp


def _ctx():
    return DepartureContext(
        category="early_departure",
        message_summary="Guest says they will leave around 9am tomorrow.",
        guest_name="Alice",
        property_name="Le Fernand",
        departure="2026-08-01",
    )


def test_happy_path_returns_structured_summary():
    client = MagicMock()
    client.messages.create.return_value = _tool_response(
        {
            "departure_status": "leaving_early",
            "certainty": "estimated",
            "estimated_time": "09:00",
            "reason": "Le voyageur annonce une heure approximative.",
            "summary_fr": "Le voyageur prévoit de partir vers 9h demain (estimation).",
        }
    )
    s = DepartureSummarizer(client=client)
    out = s.summarize(_ctx())

    assert out is not None
    assert out.departure_status == "leaving_early"
    assert out.certainty == "estimated"
    assert out.estimated_time == "09:00"
    assert "9h" in out.summary_fr


def test_llm_exception_returns_none():
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("boom")
    s = DepartureSummarizer(client=client)
    assert s.summarize(_ctx()) is None


def test_no_tool_use_returns_none():
    client = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    resp = MagicMock()
    resp.content = [text_block]
    client.messages.create.return_value = resp
    s = DepartureSummarizer(client=client)
    assert s.summarize(_ctx()) is None


def test_incomplete_tool_input_returns_none():
    client = MagicMock()
    client.messages.create.return_value = _tool_response(
        {"departure_status": "already_left"}  # missing summary_fr / certainty
    )
    s = DepartureSummarizer(client=client)
    assert s.summarize(_ctx()) is None
