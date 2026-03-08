#!/usr/bin/env python3
"""
Simulate the full end-to-end checkin automation flow without a running server.

Usage:
    # Step 1 — send a fake early check-in request to the cleaner
    python scripts/simulate.py trigger

    # Step 2 — poll for cleaner's reply and create a guest draft
    python scripts/simulate.py poll

The script shares the same DB as the web app (data/checkin.db by default).
Open http://localhost:8001/review (or the Codespace URL) to see the draft.

Environment variables are loaded from .env automatically.
"""

import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

# Allow running from the repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("simulate")


def _require(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        sys.exit(f"ERROR: {name} is not set. Add it to .env or export it.")
    return v


def _build_deps():
    from src.adapters.sqlite_memory import SqliteRequestMemory
    from src.agent.agent import AgentRunner
    from src.communication.email_notifier import EmailCleanerNotifier

    db_path = os.environ.get("DB_PATH", "data/checkin.db")
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)

    memory = SqliteRequestMemory(db_path)
    cleaner = EmailCleanerNotifier(
        smtp_host=_require("EMAIL_SMTP_HOST"),
        smtp_port=int(os.environ.get("EMAIL_SMTP_PORT", "587")),
        smtp_user=_require("EMAIL_USER"),
        smtp_password=_require("EMAIL_PASSWORD"),
        imap_host=_require("EMAIL_IMAP_HOST"),
        imap_port=int(os.environ.get("EMAIL_IMAP_PORT", "993")),
        cleaner_email=_require("CLEANER_EMAIL"),
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        dry_run=os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"),
    )
    agent = AgentRunner(
        memory=memory,
        cleaner_notifier=cleaner,
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
    )
    return memory, cleaner, agent


async def cmd_trigger():
    """
    Simulate a HostBuddy early check-in action item.
    The agent will call Claude and send a real email to the cleaner.
    """
    memory, cleaner, agent = _build_deps()

    reservation_id = 99001          # fake Smoobu booking ID
    action_item_id = f"sim-{uuid.uuid4().hex[:8]}"
    request_id = f"sim-{uuid.uuid4().hex[:12]}"
    intent = "early_checkin"
    guest_name = "Alice Dupont"
    property_name = "Apartment Les Pins"
    message_summary = "Hello, could I check in at 10am instead of 3pm? I have a flight landing at 8am."

    log.info("Saving request reservation=%d request_id=%s", reservation_id, request_id)
    await memory.save_request(
        reservation_id=reservation_id,
        intent=intent,
        request_id=request_id,
        guest_message=message_summary,
        guest_name=guest_name,
        property_name=property_name,
    )

    log.info("Running agent with event=hostbuddy_action_item ...")
    await agent.run(
        reservation_id=reservation_id,
        event_type="hostbuddy_action_item",
        event_payload={
            "action_item_id": action_item_id,
            "booking_id": str(reservation_id),
            "category": intent,
            "guest_name": guest_name,
            "property_name": property_name,
            "message_summary": message_summary,
        },
        request_id=request_id,
        intent=intent,
        guest_name=guest_name,
        property_name=property_name,
    )

    log.info(
        "Done. Check your cleaner email (%s) for the query.",
        os.environ.get("CLEANER_EMAIL"),
    )
    log.info("Once the cleaner replies, run:  python scripts/simulate.py poll")


async def cmd_poll():
    """
    Poll IMAP for cleaner replies and feed them back through the agent.
    A guest reply draft will be created in the DB (visible at /review).
    """
    memory, cleaner, agent = _build_deps()

    log.info("Polling IMAP for cleaner replies ...")
    responses = await cleaner.poll_responses()

    if not responses:
        log.info("No new cleaner replies found.")
        return

    log.info("Found %d cleaner reply(ies).", len(responses))
    for resp in responses:
        req = await memory.get_request(resp.request_id)
        if req is None:
            log.warning("Unknown request_id=%s — skipping", resp.request_id)
            continue

        log.info(
            "Processing reply for reservation=%d request=%s",
            req.reservation_id, resp.request_id,
        )
        await agent.run(
            reservation_id=req.reservation_id,
            event_type="cleaner_reply",
            event_payload={
                "request_id": resp.request_id,
                "raw_text": resp.raw_text,
            },
            request_id=req.request_id,
            intent=req.intent,
            guest_name=req.guest_name,
            property_name=req.property_name,
        )
        log.info("Draft created. Open /review to approve or reject it.")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("trigger", "poll"):
        print(__doc__)
        sys.exit("Usage: python scripts/simulate.py [trigger|poll]")

    cmd = sys.argv[1]
    if cmd == "trigger":
        asyncio.run(cmd_trigger())
    else:
        asyncio.run(cmd_poll())


if __name__ == "__main__":
    main()
