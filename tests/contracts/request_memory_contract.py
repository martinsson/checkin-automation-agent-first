"""Contract tests for any RequestMemory implementation."""

from abc import ABC, abstractmethod

import pytest

from src.ports.memory import RequestMemory


class RequestMemoryContract(ABC):

    @abstractmethod
    def create_memory(self) -> RequestMemory:
        ...

    # -- request tracking ----------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_history_returns_records(self):
        mem = self.create_memory()
        await mem.save_request(42, "early_checkin", "req-1", "msg1")
        await mem.save_request(42, "late_checkout", "req-2", "msg2")

        history = await mem.get_history(42)
        assert len(history) == 2
        intents = {r.intent for r in history}
        assert intents == {"early_checkin", "late_checkout"}

    @pytest.mark.asyncio
    async def test_get_history_empty_for_unknown_reservation(self):
        mem = self.create_memory()
        history = await mem.get_history(999)
        assert history == []

    @pytest.mark.asyncio
    async def test_get_request_by_id(self):
        mem = self.create_memory()
        await mem.save_request(42, "early_checkin", "req-1", "original msg")
        req = await mem.get_request("req-1")
        assert req is not None
        assert req.reservation_id == 42
        assert req.intent == "early_checkin"
        assert req.guest_message == "original msg"

    # -- draft management ----------------------------------------------------

    @pytest.mark.asyncio
    async def test_save_and_get_draft(self):
        mem = self.create_memory()
        await mem.save_request(42, "early_checkin", "req-1", "msg")
        draft_id = await mem.save_draft("req-1", 42, "early_checkin", "acknowledgment", "Bonjour...")

        draft = await mem.get_draft(draft_id)
        assert draft is not None
        assert draft.request_id == "req-1"
        assert draft.step == "acknowledgment"
        assert draft.draft_body == "Bonjour..."
        assert draft.verdict == "pending"

    @pytest.mark.asyncio
    async def test_pending_drafts(self):
        mem = self.create_memory()
        await mem.save_request(42, "early_checkin", "req-1", "msg")
        await mem.save_draft("req-1", 42, "early_checkin", "acknowledgment", "Draft 1")
        await mem.save_draft("req-1", 42, "early_checkin", "cleaner_query", "Draft 2")

        pending = await mem.get_pending_drafts()
        assert len(pending) == 2
        assert pending[0].step == "acknowledgment"
        assert pending[1].step == "cleaner_query"

    @pytest.mark.asyncio
    async def test_review_ok(self):
        mem = self.create_memory()
        await mem.save_request(42, "early_checkin", "req-1", "msg")
        draft_id = await mem.save_draft("req-1", 42, "early_checkin", "acknowledgment", "Draft text")

        await mem.review_draft(draft_id, "ok")

        draft = await mem.get_draft(draft_id)
        assert draft.verdict == "ok"
        assert draft.reviewed_at is not None
        # ok → no longer pending
        assert await mem.get_pending_drafts() == []

    @pytest.mark.asyncio
    async def test_review_nok_with_actual_message_and_comment(self):
        mem = self.create_memory()
        await mem.save_request(42, "early_checkin", "req-1", "msg")
        draft_id = await mem.save_draft("req-1", 42, "early_checkin", "acknowledgment", "AI draft")

        await mem.review_draft(
            draft_id, "nok",
            actual_message_sent="What I actually sent",
            owner_comment="Tone was too formal",
        )

        draft = await mem.get_draft(draft_id)
        assert draft.verdict == "nok"
        assert draft.actual_message_sent == "What I actually sent"
        assert draft.owner_comment == "Tone was too formal"
        assert draft.reviewed_at is not None

    # -- sent_at / dispatch support -------------------------------------------

    @pytest.mark.asyncio
    async def test_new_draft_has_sent_at_none(self):
        mem = self.create_memory()
        await mem.save_request(42, "early_checkin", "req-1", "msg")
        draft_id = await mem.save_draft("req-1", 42, "early_checkin", "acknowledgment", "Hi")
        draft = await mem.get_draft(draft_id)
        assert draft.sent_at is None

    # -- agent event log -------------------------------------------------------

    @pytest.mark.asyncio
    async def test_append_and_get_events_empty(self):
        mem = self.create_memory()
        events = await mem.get_events(999)
        assert events == []

    @pytest.mark.asyncio
    async def test_append_and_get_events(self):
        mem = self.create_memory()
        await mem.append_event(42, "hostbuddy_action_item", {"category": "early_checkin"})
        await mem.append_event(42, "cleaner_email_sent", {"date": "2026-03-10"})

        events = await mem.get_events(42)
        assert len(events) == 2
        assert events[0].event_type == "hostbuddy_action_item"
        assert events[0].payload == {"category": "early_checkin"}
        assert events[1].event_type == "cleaner_email_sent"

    @pytest.mark.asyncio
    async def test_events_are_reservation_scoped(self):
        mem = self.create_memory()
        await mem.append_event(42, "hostbuddy_action_item", {})
        await mem.append_event(99, "cleaner_reply", {})

        assert len(await mem.get_events(42)) == 1
        assert len(await mem.get_events(99)) == 1

    @pytest.mark.asyncio
    async def test_events_returned_oldest_first(self):
        mem = self.create_memory()
        await mem.append_event(42, "first", {})
        await mem.append_event(42, "second", {})
        await mem.append_event(42, "third", {})

        events = await mem.get_events(42)
        assert [e.event_type for e in events] == ["first", "second", "third"]

    @pytest.mark.asyncio
    async def test_event_payload_roundtrip(self):
        mem = self.create_memory()
        payload = {"a": 1, "b": "hello", "c": [1, 2, 3]}
        await mem.append_event(42, "test_event", payload)

        events = await mem.get_events(42)
        assert events[0].payload == payload
