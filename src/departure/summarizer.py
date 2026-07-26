"""
LLM step for departure notifications.

Turns a HostBuddy departure signal (category + message summary, optionally a
recent-conversation transcript) into a short, structured, French-language notice
for the cleaner. One forced tool call, mirroring the prompt-first agent.

Degrades to None on any failure so the caller can fall back to a templated
notice built from the raw message summary — a departure notification must never
be dropped just because the LLM step failed.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import anthropic

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent.parent / "prompts" / "departure_summary.txt").read_text()
_MODEL = "claude-sonnet-4-20250514"

# Precision is not critical (false positives are acceptable — the cleaner reads
# the context and decides), so a single cheap call is enough.
_TOOL = {
    "name": "report_departure",
    "description": "Report the structured departure summary for the cleaner.",
    "input_schema": {
        "type": "object",
        "properties": {
            "departure_status": {
                "type": "string",
                "enum": ["already_left", "leaving_early"],
                "description": "already_left = guest states they have left; "
                "leaving_early = guest intends/asks to leave earlier than standard.",
            },
            "certainty": {
                "type": "string",
                "enum": ["confirmed", "estimated"],
                "description": "confirmed = explicitly stated; estimated = an "
                "intention or approximate time only.",
            },
            "estimated_time": {
                "type": ["string", "null"],
                "description": "The time mentioned (e.g. '09:30'), or null if none.",
            },
            "reason": {
                "type": "string",
                "description": "One short French sentence: why it is confirmed/estimated.",
            },
            "summary_fr": {
                "type": "string",
                "description": "1-3 French sentences summarising the essentials for the cleaner.",
            },
        },
        "required": ["departure_status", "certainty", "summary_fr"],
    },
}


@dataclass
class DepartureContext:
    """Everything the summarizer needs about one departure signal."""

    category: str            # "guest_left" | "early_departure"
    message_summary: str     # HostBuddy's AI summary of the situation
    guest_name: str = ""
    property_name: str = ""
    arrival: str = ""
    departure: str = ""
    transcript: str = ""     # recent conversation text, when available


@dataclass
class DepartureSummary:
    departure_status: str
    certainty: str
    summary_fr: str
    reason: str = ""
    estimated_time: str | None = None


class DepartureSummarizer:
    """Summarise a departure signal into a structured French notice."""

    def __init__(self, anthropic_api_key: str | None = None, client=None):
        self._client = client or anthropic.Anthropic(
            api_key=anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        )

    def summarize(self, ctx: DepartureContext) -> DepartureSummary | None:
        """Return a structured summary, or None if the LLM step fails."""
        try:
            response = self._client.messages.create(
                model=_MODEL,
                max_tokens=600,
                system=_SYSTEM_PROMPT,
                tools=[_TOOL],
                tool_choice={"type": "tool", "name": "report_departure"},
                messages=[{"role": "user", "content": self._build_user_turn(ctx)}],
            )
        except Exception as exc:  # network, auth, rate limit, etc.
            log.warning("Departure summarizer: LLM call failed — %s", exc)
            return None

        tool_calls = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if not tool_calls:
            log.warning("Departure summarizer: no tool_use block in response")
            return None

        data = tool_calls[0].input or {}
        summary_fr = str(data.get("summary_fr") or "").strip()
        status = str(data.get("departure_status") or "").strip()
        certainty = str(data.get("certainty") or "").strip()
        if not summary_fr or not status or not certainty:
            log.warning("Departure summarizer: incomplete tool input %r", data)
            return None

        est = data.get("estimated_time")
        return DepartureSummary(
            departure_status=status,
            certainty=certainty,
            summary_fr=summary_fr,
            reason=str(data.get("reason") or "").strip(),
            estimated_time=str(est).strip() if est else None,
        )

    @staticmethod
    def _build_user_turn(ctx: DepartureContext) -> str:
        lines = [
            f"Catégorie détectée : {ctx.category}",
            f"Logement : {ctx.property_name or '?'}",
            f"Voyageur : {ctx.guest_name or '?'}",
            f"Dates : arrivée {ctx.arrival or '?'} → départ {ctx.departure or '?'}",
            "",
            "Résumé de la messagerie :",
            ctx.message_summary or "(aucun résumé fourni)",
        ]
        if ctx.transcript.strip():
            lines += ["", "Extrait de conversation récente :", ctx.transcript.strip()]
        return "\n".join(lines)
