"""
Build the user-turn prompt from the agent event log.

The full history is formatted as a chronological narrative so Claude
can understand the current state without any additional state machine.
"""

from src.ports.memory import AgentEvent

_FRENCH_MARKERS = {
    "bonjour", "salut", "merci", "serait", "possible", "arriver", "pourrait",
    "souhaite", "pourrais", "voudrais", "s'il", "nous", "je", "est-ce",
    "au lieu de", "départ", "arrivée", "heure",
}


def _detect_language(text: str) -> str:
    """Simple heuristic: if the text contains French words, it's French."""
    lower = text.lower()
    french_count = sum(1 for w in _FRENCH_MARKERS if w in lower)
    return "French" if french_count >= 2 else "English"


_EVENT_LABELS = {
    "hostbuddy_action_item": "GUEST REQUEST (from HostBuddy)",
    "cleaner_email_sent": "CLEANER EMAIL SENT",
    "cleaner_reply": "CLEANER REPLY RECEIVED",
    "guest_draft_created": "GUEST REPLY DRAFT CREATED (awaiting owner review)",
    "wait": "WAIT (no action taken)",
}


def build_history_prompt(events: list[AgentEvent]) -> str:
    """
    Format the event log as the user turn.

    Returns a multi-line string with each event on its own block,
    oldest first. The final line asks Claude what to do next.
    """
    if not events:
        return "No events yet for this reservation."

    lines: list[str] = ["## Request history\n"]

    for i, event in enumerate(events, start=1):
        label = _EVENT_LABELS.get(event.event_type, event.event_type.upper())
        lines.append(f"### Event {i}: {label}")
        lines.append(f"*{event.created_at.strftime('%Y-%m-%d %H:%M UTC')}*\n")

        payload = event.payload
        if event.event_type == "hostbuddy_action_item":
            msg = payload.get("message_summary", "")
            guest_lang = _detect_language(msg)
            lines.append(f"- **Guest:** {payload.get('guest_name', '?')}")
            lines.append(f"- **Property:** {payload.get('property_name', '?')}")
            lines.append(f"- **Category:** {payload.get('category', '?')}")
            lines.append(f"- **Message summary:** {msg}")
            lines.append(f"- **Guest language:** {guest_lang} ← reply to guest in this language")
            lines.append(f"- **Booking ID:** {payload.get('booking_id', '?')}")

        elif event.event_type == "cleaner_email_sent":
            lines.append(f"- **Date:** {payload.get('date', '?')}")
            lines.append(f"- **Message sent:** {payload.get('message', '?')}")

        elif event.event_type == "cleaner_reply":
            lines.append(f"- **Reply text:** {payload.get('raw_text', '?')}")

        elif event.event_type == "guest_draft_created":
            lines.append(f"- **Draft body:** {payload.get('body', '?')}")

        elif event.event_type == "wait":
            reason = payload.get("reason", "")
            if reason:
                lines.append(f"- **Reason:** {reason}")

        else:
            for k, v in payload.items():
                lines.append(f"- **{k}:** {v}")

        lines.append("")  # blank line between events

    lines.append("---")
    lines.append(
        "Based on the history above, decide what to do next. "
        "You MUST call exactly one tool."
    )

    return "\n".join(lines)
