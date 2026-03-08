"""
HostBuddy action item webhook endpoint.

POST /webhook/hostbuddy receives action items from HostBuddy AI.
Only early_checkin and late_checkout categories trigger the agent.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

router = APIRouter()

_HANDLED_CATEGORIES = {"early_checkin", "late_checkout"}


class HostBuddyPayload(BaseModel):
    """
    Normalised shape of a HostBuddy action item webhook.

    Unknown fields are ignored (model_config extra='ignore').
    """

    model_config = {"extra": "ignore"}

    action_item_id: str
    booking_id: str          # maps to reservation_id in Smoobu (cast to int later)
    category: str            # e.g. "early_checkin", "late_checkout", "noise_complaint"
    guest_name: str = ""
    property_name: str = ""
    message_summary: str = ""


@router.post("/webhook/hostbuddy")
async def hostbuddy_webhook(request: Request):
    raw = None
    try:
        raw = await request.json()
        payload = HostBuddyPayload.model_validate(raw)
    except ValidationError as exc:
        log.warning("HostBuddy webhook: invalid payload — %s — raw: %s", exc, raw)
        return JSONResponse(status_code=422, content={"error": "invalid payload"})
    except Exception as exc:
        log.warning("HostBuddy webhook: failed to parse body — %s", exc)
        return JSONResponse(status_code=422, content={"error": "parse error"})

    # Ignore categories we don't handle
    if payload.category not in _HANDLED_CATEGORIES:
        log.info(
            "HostBuddy webhook: ignoring category=%r action_item_id=%s",
            payload.category,
            payload.action_item_id,
        )
        return JSONResponse({"status": "ignored"})

    reservation_id = int(payload.booking_id)
    memory = request.app.state.memory
    agent = request.app.state.agent

    # Idempotency: if this action_item_id is already in the event log, skip
    existing_events = await memory.get_events(reservation_id)
    for event in existing_events:
        if (
            event.event_type == "hostbuddy_action_item"
            and event.payload.get("action_item_id") == payload.action_item_id
        ):
            log.info(
                "HostBuddy webhook: duplicate action_item_id=%s — skipping",
                payload.action_item_id,
            )
            return JSONResponse({"status": "duplicate"})

    # Look up the existing request for this reservation+intent (may already exist from
    # a previous cycle), or we'll create one inline if needed.
    existing_request = None
    history = await memory.get_history(reservation_id)
    for req in history:
        if req.intent == payload.category:
            existing_request = req
            break

    # If no request exists yet, create one now so the agent has a request_id to attach
    # drafts and cleaner emails to.
    if existing_request is None:
        import uuid
        request_id = f"hb-{uuid.uuid4().hex[:12]}"
        await memory.save_request(
            reservation_id=reservation_id,
            intent=payload.category,
            request_id=request_id,
            guest_message=payload.message_summary,
            guest_name=payload.guest_name,
            property_name=payload.property_name,
        )
        intent = payload.category
    else:
        request_id = existing_request.request_id
        intent = existing_request.intent

    # Fire the agent
    await agent.run(
        reservation_id=reservation_id,
        event_type="hostbuddy_action_item",
        event_payload={
            "action_item_id": payload.action_item_id,
            "booking_id": payload.booking_id,
            "category": payload.category,
            "guest_name": payload.guest_name,
            "property_name": payload.property_name,
            "message_summary": payload.message_summary,
        },
        request_id=request_id,
        intent=intent,
        guest_name=payload.guest_name,
        property_name=payload.property_name,
    )

    return JSONResponse({"status": "accepted"})
