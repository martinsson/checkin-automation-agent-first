"""Contract tests for any RequestMemory implementation."""

from abc import ABC, abstractmethod

import pytest

from src.ports.memory import RequestMemory


class RequestMemoryContract(ABC):

    @abstractmethod
    def create_memory(self) -> RequestMemory:
        ...

    # -- message-level dedup -------------------------------------------------

    @pytest.mark.asyncio
    async def test_message_not_seen_by_default(self):
        mem = self.create_memory()
        assert await mem.has_message_been_seen(999) is False

    @pytest.mark.asyncio
    async def test_mark_and_check_message_seen(self):
        mem = self.create_memory()
        await mem.mark_message_seen(42, 101)
        assert await mem.has_message_been_seen(42) is True

    @pytest.mark.asyncio
    async def test_mark_message_seen_is_idempotent(self):
        mem = self.create_memory()
        await mem.mark_message_seen(42, 101)
        await mem.mark_message_seen(42, 101)  # must not raise
        assert await mem.has_message_been_seen(42) is True

    # -- request tracking ----------------------------------------------------

    @pytest.mark.asyncio
    async def test_not_processed_by_default(self):
        mem = self.create_memory()
        assert await mem.has_been_processed(42, "early_checkin") is False

    @pytest.mark.asyncio
    async def test_save_then_check(self):
        mem = self.create_memory()
        await mem.save_request(42, "early_checkin", "req-1", "Can I check in early?")
        assert await mem.has_been_processed(42, "early_checkin") is True

    @pytest.mark.asyncio
    async def test_different_intents_are_independent(self):
        mem = self.create_memory()
        await mem.save_request(42, "early_checkin", "req-1", "msg")
        assert await mem.has_been_processed(42, "late_checkout") is False

    @pytest.mark.asyncio
    async def test_different_reservations_are_independent(self):
        mem = self.create_memory()
        await mem.save_request(42, "early_checkin", "req-1", "msg")
        assert await mem.has_been_processed(99, "early_checkin") is False

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

    @pytest.mark.asyncio
    async def test_update_status(self):
        mem = self.create_memory()
        await mem.save_request(42, "early_checkin", "req-1", "msg")
        await mem.update_status("req-1", "pending_cleaner")
        req = await mem.get_request("req-1")
        assert req.status == "pending_cleaner"

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

    @pytest.mark.asyncio
    async def test_get_reviewed_unsent_drafts(self):
        mem = self.create_memory()
        await mem.save_request(42, "early_checkin", "req-1", "msg")

        # pending draft — should NOT appear
        await mem.save_draft("req-1", 42, "early_checkin", "acknowledgment", "Draft pending")

        # reviewed ok, unsent — SHOULD appear
        d_ok = await mem.save_draft("req-1", 42, "early_checkin", "cleaner_query", "Draft ok")
        await mem.review_draft(d_ok, "ok")

        # reviewed nok, unsent — SHOULD appear
        d_nok = await mem.save_draft("req-1", 42, "early_checkin", "guest_reply", "Draft nok")
        await mem.review_draft(d_nok, "nok", actual_message_sent="Fixed text")

        # reviewed ok, already sent — should NOT appear
        d_sent = await mem.save_draft("req-1", 42, "early_checkin", "followup", "Draft sent")
        await mem.review_draft(d_sent, "ok")
        await mem.mark_draft_sent(d_sent)

        unsent = await mem.get_reviewed_unsent_drafts()
        ids = [d.draft_id for d in unsent]
        assert d_ok in ids
        assert d_nok in ids
        assert len(unsent) == 2

    @pytest.mark.asyncio
    async def test_mark_draft_sent(self):
        mem = self.create_memory()
        await mem.save_request(42, "early_checkin", "req-1", "msg")
        draft_id = await mem.save_draft("req-1", 42, "early_checkin", "acknowledgment", "Hi")
        await mem.review_draft(draft_id, "ok")

        await mem.mark_draft_sent(draft_id)

        draft = await mem.get_draft(draft_id)
        assert draft.sent_at is not None
        # no longer appears in unsent
        unsent = await mem.get_reviewed_unsent_drafts()
        assert all(d.draft_id != draft_id for d in unsent)

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
