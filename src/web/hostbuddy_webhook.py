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
# Departure-forward signals → the one-way cleaner-notification flow (not the
# early/late-checkout agent). Both mean the cleaner may be able to come earlier.
# TODO(owner): confirm these exact category strings against the Custom Action
# Item Categories you configure in the HostBuddy dashboard, then adjust here.
_DEPARTURE_CATEGORIES = {"guest_left", "early_departure"}


class HostBuddyPayload(BaseModel):
    """
    Normalised shape of a HostBuddy action item webhook.

    Unknown fields are ignored (model_config extra='ignore').

    TODO(owner): finalise these field names/casing against a real captured
    HostBuddy POST — the payload schema is not publicly documented. `extra`
    stays 'ignore' so an unexpected/renamed field won't 422 us in the meantime.
    """

    model_config = {"extra": "ignore"}

    action_item_id: str
    booking_id: str          # maps to reservation_id in Smoobu (cast to int later)
    category: str            # e.g. "early_checkin", "late_checkout", "noise_complaint"
    guest_name: str = ""
    property_name: str = ""
    message_summary: str = ""
    # Optional: present only if a HostBuddy Journey/webhook can be configured to
    # include a recent-conversation excerpt (to verify in the dashboard). When
    # absent the departure flow falls back to message_summary.
    transcript: str = ""


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

    # Departure-forward signals go to the one-way cleaner-notification flow.
    if payload.category in _DEPARTURE_CATEGORIES:
        return await _handle_departure(request, payload)

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

    # Idempotency: mark action_item_id on first delivery, reject duplicates
    if await memory.has_action_item_been_seen(payload.action_item_id):
        log.info(
            "HostBuddy webhook: duplicate action_item_id=%s — skipping",
            payload.action_item_id,
        )
        return JSONResponse({"status": "duplicate"})
    await memory.mark_action_item_seen(payload.action_item_id)

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

    return JSONResponse({"status": "accepted", "request_id": request_id})


async def _handle_departure(request: Request, payload: HostBuddyPayload) -> JSONResponse:
    """Route a departure action item to the one-way cleaner notification flow."""
    service = getattr(request.app.state, "departure_service", None)
    if service is None:
        log.error("HostBuddy webhook: departure_service not configured — cannot notify")
        return JSONResponse(status_code=503, content={"error": "departure flow unavailable"})

    memory = request.app.state.memory
    # HTTP-level idempotency on the literal delivery (the service also dedups per
    # booking+category). Mark only after a successful handle so a transient send
    # failure can be retried by a re-delivery.
    if await memory.has_action_item_been_seen(payload.action_item_id):
        log.info("HostBuddy webhook: duplicate departure action_item_id=%s",
                 payload.action_item_id)
        return JSONResponse({"status": "duplicate"})

    from src.departure.service import DeparturePayload

    result = await service.handle(
        DeparturePayload(
            action_item_id=payload.action_item_id,
            booking_id=int(payload.booking_id),
            category=payload.category,
            guest_name=payload.guest_name,
            property_name=payload.property_name,
            message_summary=payload.message_summary,
            transcript=payload.transcript,
        )
    )

    if result.get("status") in ("sent", "duplicate"):
        await memory.mark_action_item_seen(payload.action_item_id)

    return JSONResponse({"status": result.get("status", "error")})


# ---------------------------------------------------------------------------
# Cleaner reply injection (for testing without real IMAP)
# ---------------------------------------------------------------------------


class CleanerReplyPayload(BaseModel):
    """Inject a cleaner reply to continue the agent flow."""

    model_config = {"extra": "ignore"}

    reservation_id: int
    request_id: str
    reply_text: str


@router.post("/webhook/cleaner-reply")
async def cleaner_reply_webhook(request: Request):
    """Inject a cleaner reply event and re-run the agent."""
    raw = None
    try:
        raw = await request.json()
        payload = CleanerReplyPayload.model_validate(raw)
    except ValidationError as exc:
        log.warning("cleaner-reply: invalid payload — %s — raw: %s", exc, raw)
        return JSONResponse(status_code=422, content={"error": "invalid payload"})
    except Exception as exc:
        log.warning("cleaner-reply: failed to parse body — %s", exc)
        return JSONResponse(status_code=422, content={"error": "parse error"})

    agent = request.app.state.agent

    await agent.run(
        reservation_id=payload.reservation_id,
        event_type="cleaner_reply",
        event_payload={"raw_text": payload.reply_text},
        request_id=payload.request_id,
    )

    return JSONResponse({"status": "accepted"})
