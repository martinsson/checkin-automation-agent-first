"""
Prompt-first agent for early check-in / late checkout requests.

One Claude call per event. Full history is passed as context.
Claude responds with tool_use blocks only — the agent dispatches them.
"""

import logging
import os
from pathlib import Path

import anthropic

from src.agent.history import build_history_prompt
from src.agent.tools import TOOLS
from src.ports.cleaner import CleanerNotifier, CleanerQuery
from src.ports.door_lock import DoorCodeRequest, DoorLockError, DoorLockGateway
from src.ports.memory import RequestMemory

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent.parent / "prompts" / "agent_system.txt").read_text()
_MODEL = "claude-sonnet-4-20250514"


class AgentRunner:
    """
    Runs one Claude call per event, dispatches the resulting tool call,
    and appends the result to the event log.
    """

    def __init__(
        self,
        memory: RequestMemory,
        cleaner_notifier: CleanerNotifier,
        anthropic_api_key: str | None = None,
        door_lock: DoorLockGateway | None = None,
    ):
        self._memory = memory
        self._notifier = cleaner_notifier
        self._door_lock = door_lock
        self._client = anthropic.Anthropic(
            api_key=anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        )

    async def run(
        self,
        reservation_id: int,
        event_type: str,
        event_payload: dict,
        *,
        # Context fields needed by tools (come from the reservation / action item)
        request_id: str = "",
        intent: str = "",
        guest_name: str = "",
        property_name: str = "",
        cleaner_name: str = "",
        original_time: str = "",
        requested_time: str = "",
    ) -> None:
        """
        Append the new event, load full history, call Claude, dispatch tool.
        """
        # 1. Append the new event before calling Claude so it's in the history
        await self._memory.append_event(reservation_id, event_type, event_payload)

        # 2. Load full history (now includes the event we just appended)
        events = await self._memory.get_events(reservation_id)

        # 3. Build the user turn
        user_prompt = build_history_prompt(events)

        # 4. Call Claude
        log.info(
            "Agent: calling Claude for reservation=%s event=%s", reservation_id, event_type
        )
        response = self._client.messages.create(
            model=_MODEL,
            max_tokens=1000,
            system=_SYSTEM_PROMPT,
            tools=TOOLS,
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": user_prompt}],
        )

        # 5. Find the tool_use block
        tool_calls = [b for b in response.content if b.type == "tool_use"]
        text_blocks = [b for b in response.content if b.type == "text"]

        if text_blocks:
            log.warning(
                "Agent: Claude returned text instead of a tool call: %s",
                text_blocks[0].text[:200],
            )

        if not tool_calls:
            log.warning(
                "Agent: no tool_use block in Claude response for reservation=%s — "
                "appending wait event",
                reservation_id,
            )
            await self._memory.append_event(
                reservation_id, "wait", {"reason": "Claude returned no tool call"}
            )
            return

        tool_call = tool_calls[0]
        tool_name = tool_call.name
        tool_input = tool_call.input

        log.info("Agent: dispatching tool=%s input=%s", tool_name, tool_input)

        # 6. Dispatch
        if tool_name == "send_cleaner_email":
            await self._handle_send_cleaner_email(
                reservation_id=reservation_id,
                request_id=request_id,
                intent=intent,
                guest_name=guest_name,
                property_name=property_name,
                cleaner_name=cleaner_name,
                original_time=original_time,
                requested_time=requested_time,
                date=tool_input.get("date", ""),
                message=tool_input.get("message", ""),
            )

        elif tool_name == "create_guest_draft":
            await self._handle_create_guest_draft(
                reservation_id=reservation_id,
                request_id=request_id,
                intent=intent,
                body=tool_input.get("body", ""),
            )

        elif tool_name == "create_door_code":
            await self._handle_create_door_code(
                reservation_id=reservation_id,
                guest_name=guest_name,
                starts_at=tool_input.get("starts_at", ""),
                ends_at=tool_input.get("ends_at", ""),
                code_name=tool_input.get("code_name", ""),
            )

        elif tool_name == "wait":
            reason = tool_input.get("reason", "")
            await self._memory.append_event(reservation_id, "wait", {"reason": reason})
            log.info("Agent: wait — %s", reason)

        else:
            log.error("Agent: unknown tool %r — appending wait", tool_name)
            await self._memory.append_event(
                reservation_id, "wait", {"reason": f"Unknown tool: {tool_name}"}
            )

    # -- tool handlers ---------------------------------------------------------

    async def _handle_send_cleaner_email(
        self,
        *,
        reservation_id: int,
        request_id: str,
        intent: str,
        guest_name: str,
        property_name: str,
        cleaner_name: str,
        original_time: str,
        requested_time: str,
        date: str,
        message: str,
    ) -> None:
        query = CleanerQuery(
            request_id=request_id,
            cleaner_name=cleaner_name or "Équipe ménage",
            guest_name=guest_name,
            property_name=property_name,
            request_type=intent,
            original_time=original_time,
            requested_time=requested_time,
            date=date,
            message=message,
        )
        tracking_id = await self._notifier.send_query(query)
        log.info("Agent: cleaner email sent tracking_id=%s", tracking_id)
        await self._memory.append_event(
            reservation_id,
            "cleaner_email_sent",
            {"date": date, "message": message, "tracking_id": tracking_id},
        )

    async def _handle_create_door_code(
        self,
        *,
        reservation_id: int,
        guest_name: str,
        starts_at: str,
        ends_at: str,
        code_name: str,
    ) -> None:
        if self._door_lock is None:
            log.error(
                "Agent: create_door_code called but no door lock gateway is configured "
                "(set MAKE_IGLOOHOME_WEBHOOK_URL)"
            )
            await self._memory.append_event(
                reservation_id,
                "door_code_failed",
                {"error": "No door lock gateway configured (MAKE_IGLOOHOME_WEBHOOK_URL)"},
            )
            return

        request = DoorCodeRequest(
            person_name=guest_name,
            starts_at=starts_at,
            ends_at=ends_at,
            purpose="early_checkin",
            reservation_id=reservation_id,
            code_name=code_name or f"{guest_name} — resa {reservation_id}".strip(" —"),
        )
        try:
            door_code = await self._door_lock.create_code(request)
        except DoorLockError as exc:
            log.error("Agent: door code creation failed: %s", exc)
            await self._memory.append_event(
                reservation_id,
                "door_code_failed",
                {"error": str(exc), "starts_at": starts_at, "ends_at": ends_at},
            )
            return

        log.info("Agent: door code created code_id=%s", door_code.code_id)
        await self._memory.append_event(
            reservation_id,
            "door_code_created",
            {
                "code": door_code.code,
                "code_id": door_code.code_id,
                "name": door_code.name,
                "starts_at": starts_at,
                "ends_at": ends_at,
            },
        )

    async def _handle_create_guest_draft(
        self,
        *,
        reservation_id: int,
        request_id: str,
        intent: str,
        body: str,
    ) -> None:
        draft_id = await self._memory.save_draft(
            request_id=request_id,
            reservation_id=reservation_id,
            intent=intent,
            step="guest_reply",
            draft_body=body,
        )
        log.info("Agent: guest draft saved draft_id=%s", draft_id)
        await self._memory.append_event(
            reservation_id,
            "guest_draft_created",
            {"draft_id": draft_id, "body": body},
        )
