#!/usr/bin/env python3
"""
Interactive end-to-end test script for the deployed checkin automation.

Usage:
    python scripts/test_flow.py [--base-url URL]

Default URL: http://95.217.21.76:8001

Flow:
  1. Sends a HostBuddy webhook (guest request for early check-in)
  2. Shows the agent's actions (cleaner email or immediate guest draft)
  3. If a cleaner email was sent, prompts you for the cleaner's reply
  4. Injects the cleaner reply and shows the resulting guest draft
"""

import argparse
import json
import sys
import time
import uuid

import requests

DEFAULT_BASE = "http://95.217.21.76:8001"


def post(base: str, path: str, payload: dict) -> dict:
    url = f"{base}{path}"
    r = requests.post(url, json=payload, timeout=30)
    print(f"  POST {path} → {r.status_code}")
    data = r.json()
    print(f"  Response: {json.dumps(data, indent=2)}")
    return data


def get_events(base: str, reservation_id: int) -> list[dict]:
    url = f"{base}/events/{reservation_id}"
    r = requests.get(url, timeout=10)
    return r.json()


def print_events(events: list[dict], start_from: int = 0):
    for i, e in enumerate(events):
        if i < start_from:
            continue
        etype = e["event_type"]
        payload = e["payload"]
        ts = e["created_at"]
        print(f"\n  [{i+1}] {etype}  ({ts})")
        if etype == "hostbuddy_action_item":
            print(f"      Guest: {payload.get('guest_name')}")
            print(f"      Property: {payload.get('property_name')}")
            print(f"      Category: {payload.get('category')}")
            print(f"      Message: {payload.get('message_summary')}")
        elif etype == "cleaner_email_sent":
            print(f"      Date: {payload.get('date')}")
            print(f"      Message to cleaner:")
            for line in payload.get("message", "").split("\n"):
                print(f"        {line}")
        elif etype == "cleaner_reply":
            print(f"      Reply: {payload.get('raw_text')}")
        elif etype == "guest_draft_created":
            print(f"      Draft #{payload.get('draft_id')}:")
            for line in payload.get("body", "").split("\n"):
                print(f"        {line}")
        elif etype == "wait":
            print(f"      Reason: {payload.get('reason')}")
        else:
            print(f"      {json.dumps(payload, indent=6)}")


def main():
    parser = argparse.ArgumentParser(description="Test the checkin automation flow")
    parser.add_argument("--base-url", default=DEFAULT_BASE, help=f"Base URL (default: {DEFAULT_BASE})")
    parser.add_argument("--guest-name", default="Alice Dupont")
    parser.add_argument("--property", default="Apartment Les Pins")
    parser.add_argument("--category", default="early_checkin", choices=["early_checkin", "late_checkout"])
    parser.add_argument("--message", default="Bonjour, serait-il possible d'arriver à 14h au lieu de 17h ? Notre vol arrive à 11h.")
    parser.add_argument("--booking-id", default=None, help="Booking ID (default: random)")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    booking_id = args.booking_id or str(90000 + int(uuid.uuid4().hex[:4], 16) % 10000)
    reservation_id = int(booking_id)

    # ── Step 1: Send HostBuddy webhook ────────────────────────────────
    print("\n═══ Step 1: Sending HostBuddy webhook (guest request) ═══")
    print(f"  Guest: {args.guest_name}")
    print(f"  Property: {args.property}")
    print(f"  Category: {args.category}")
    print(f"  Message: {args.message}")
    print(f"  Booking ID: {booking_id}")
    print()

    webhook_payload = {
        "action_item_id": f"test-{uuid.uuid4().hex[:8]}",
        "booking_id": booking_id,
        "category": args.category,
        "guest_name": args.guest_name,
        "property_name": args.property,
        "message_summary": args.message,
    }
    result = post(base, "/webhook/hostbuddy", webhook_payload)

    if result.get("status") != "accepted":
        print("\n  Webhook was not accepted. Exiting.")
        sys.exit(1)

    request_id = result.get("request_id", "")
    print(f"  request_id: {request_id}")

    # ── Step 2: Show what the agent did ───────────────────────────────
    print("\n═══ Step 2: Agent actions ═══")
    events = get_events(base, reservation_id)
    print_events(events)

    # Check what happened
    cleaner_email = None
    guest_draft = None
    for e in events:
        if e["event_type"] == "cleaner_email_sent":
            cleaner_email = e["payload"]
        if e["event_type"] == "guest_draft_created":
            guest_draft = e["payload"]

    if guest_draft:
        print("\n═══ Done! The agent created a guest draft directly (no cleaner needed). ═══")
        print(f"  Check {base}/review to approve or reject it.")
        return

    if not cleaner_email:
        print("\n  No cleaner email and no guest draft. Check events above.")
        return

    # ── Step 3: Prompt for cleaner reply ──────────────────────────────
    print("\n═══ Step 3: Cleaner email was sent. Now provide the cleaner's reply. ═══")
    print("  (The email above is what the cleaner would receive)")
    print()

    reply = input("  Enter cleaner's reply text (or 'skip' to exit): ").strip()
    if reply.lower() == "skip" or not reply:
        print("  Skipping. You can poll for real cleaner replies later.")
        return

    # ── Step 4: Inject cleaner reply ──────────────────────────────────
    print(f"\n═══ Step 4: Injecting cleaner reply (request_id={request_id}) ═══")
    reply_payload = {
        "reservation_id": reservation_id,
        "request_id": request_id,
        "reply_text": reply,
    }
    result = post(base, "/webhook/cleaner-reply", reply_payload)

    if result.get("status") != "accepted":
        print("  Cleaner reply was not accepted. Exiting.")
        sys.exit(1)

    # ── Step 5: Show the guest draft ──────────────────────────────────
    print("\n═══ Step 5: Agent response after cleaner reply ═══")
    time.sleep(1)
    new_events = get_events(base, reservation_id)
    print_events(new_events, start_from=len(events))

    print(f"\n═══ Done! Check {base}/review to approve or reject the draft. ═══")


if __name__ == "__main__":
    main()
