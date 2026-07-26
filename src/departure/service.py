"""
Departure notification orchestrator.

Entry point for the "guest left / leaving early" flow. A HostBuddy departure
action item (see src/web/hostbuddy_webhook.py) carries only a booking id, a
category and a short message summary — this service enriches it from Beds24
(guest phone, propertyId, dates), asks the LLM for a short French summary,
resolves the property's cleaner, and sends a one-way email.

Graceful degradation is deliberate — a departure notice must never be dropped:
  * Beds24 enrichment fails  → send from the webhook payload, without a phone.
  * LLM summariser fails      → template the notice from the message summary.
  * property has no cleaner   → fall back to the default CLEANER_EMAIL.
"""

import logging
from dataclasses import dataclass

from src.config.cleaner_map import CleanerMap
from src.departure.summarizer import DepartureContext, DepartureSummarizer, DepartureSummary
from src.ports.cleaner import CleanerNotifier, DepartureNotice
from src.ports.memory import RequestMemory
from src.ports.reservations import BookingGatewayError

log = logging.getLogger(__name__)

# HostBuddy category → our internal departure_status default.
_CATEGORY_STATUS = {
    "guest_left": "already_left",
    "early_departure": "leaving_early",
}


@dataclass
class DeparturePayload:
    """The bits of a HostBuddy departure action item this flow needs."""

    action_item_id: str
    booking_id: int
    category: str            # "guest_left" | "early_departure"
    guest_name: str = ""
    property_name: str = ""
    message_summary: str = ""
    transcript: str = ""     # optional, if the webhook ever carries one


class DepartureNotificationService:
    """Turn a departure signal into a one-way cleaner email."""

    def __init__(
        self,
        memory: RequestMemory,
        notifier: CleanerNotifier,
        cleaner_map: CleanerMap,
        summarizer: DepartureSummarizer,
        booking_gateway=None,  # duck-typed: needs async get_booking(id); may be None
    ):
        self._memory = memory
        self._notifier = notifier
        self._cleaner_map = cleaner_map
        self._summarizer = summarizer
        self._booking_gateway = booking_gateway

    async def handle(self, payload: DeparturePayload) -> dict:
        """Enrich → summarise → route → send. Returns a small status dict."""
        # Cross-delivery dedup: one notice per (booking, condition), independent
        # of the webhook's action_item_id idempotency.
        dedup_key = f"dep:{payload.booking_id}:{payload.category}"
        if await self._memory.has_action_item_been_seen(dedup_key):
            log.info("Departure: already notified for %s — skipping", dedup_key)
            return {"status": "duplicate"}

        # 1. Enrich from Beds24 (phone, propertyId, dates). Degrade on any failure.
        phone = ""
        property_id = None
        departure_date = ""
        guest_name = payload.guest_name
        arrival = ""
        if self._booking_gateway is not None:
            try:
                booking = await self._booking_gateway.get_booking(payload.booking_id)
            except BookingGatewayError as exc:
                log.warning("Departure: Beds24 enrichment failed for %s — %s",
                            payload.booking_id, exc)
                booking = None
            except Exception as exc:  # never let enrichment sink the notice
                log.warning("Departure: unexpected enrichment error for %s — %s",
                            payload.booking_id, exc)
                booking = None
            if booking is not None:
                phone = booking.phone or ""
                property_id = booking.property_id or None
                departure_date = booking.departure or ""
                arrival = booking.arrival or ""
                guest_name = guest_name or booking.guest_name

        # 2. Resolve the property's cleaner (default fallback baked into the map).
        contact = self._cleaner_map.for_property(property_id)

        # 3. Summarise (LLM), with a templated fallback.
        summary = self._summarizer.summarize(
            DepartureContext(
                category=payload.category,
                message_summary=payload.message_summary,
                guest_name=guest_name,
                property_name=payload.property_name,
                arrival=arrival,
                departure=departure_date,
                transcript=payload.transcript,
            )
        )
        if summary is None:
            summary = self._fallback_summary(payload)
            used_llm = False
        else:
            used_llm = True

        # 4. Build and send the one-way notice.
        notice = DepartureNotice(
            property_name=payload.property_name,
            guest_name=guest_name,
            departure_date=departure_date,
            summary_fr=summary.summary_fr,
            departure_status=summary.departure_status,
            certainty=summary.certainty,
            estimated_time=summary.estimated_time,
            guest_phone=phone,
        )
        try:
            tracking_id = await self._notifier.send_departure_notice(contact.email, notice)
        except Exception as exc:
            # Don't mark dedup — allow a later re-delivery to retry.
            log.error("Departure: send failed for %s — %s", dedup_key, exc)
            return {"status": "error", "error": str(exc)}

        # 5. Mark dedup + log the event only after a successful send.
        await self._memory.mark_action_item_seen(dedup_key)
        await self._memory.append_event(
            payload.booking_id,
            "departure_notified",
            {
                "category": payload.category,
                "cleaner_email": contact.email,
                "phone_included": bool(phone),
                "used_llm": used_llm,
                "departure_status": summary.departure_status,
                "certainty": summary.certainty,
                "summary_fr": summary.summary_fr,
                "tracking_id": tracking_id,
            },
        )
        log.info(
            "Departure notice sent for booking=%s category=%s to=%s (llm=%s, phone=%s)",
            payload.booking_id, payload.category, contact.email, used_llm, bool(phone),
        )
        return {"status": "sent", "tracking_id": tracking_id}

    @staticmethod
    def _fallback_summary(payload: DeparturePayload) -> DepartureSummary:
        """Templated summary when the LLM step is unavailable — uses HostBuddy's
        own message summary verbatim so the cleaner still gets the context."""
        status = _CATEGORY_STATUS.get(payload.category, "")
        base = (payload.message_summary or "").strip()
        if status == "already_left":
            summary_fr = "Le voyageur indique être parti du logement."
        elif status == "leaving_early":
            summary_fr = "Le voyageur souhaite partir plus tôt que prévu."
        else:
            summary_fr = "Signal de départ du voyageur."
        if base:
            summary_fr += f" Détail reçu : {base}"
        return DepartureSummary(
            departure_status=status,
            certainty="",  # unknown without the LLM
            summary_fr=summary_fr,
        )
