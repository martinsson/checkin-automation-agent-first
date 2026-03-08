# CONTEXT.md — Claude Code Onboarding

This file summarises the full build session for the early check-in / late checkout
automation feature. Hand it to a new Claude Code session to pick up exactly where
we left off.

---

## What this project is

An automation layer on top of an Airbnb property managed via **Smoobu** PMS and
**HostBuddy AI** messaging. When a guest requests early check-in or late checkout,
the owner previously had to manually forward the request to their cleaner, wait for
a reply, then respond to the guest. This project eliminates that loop.

---

## What we built in this session

### Feature: Prompt-first agent for early check-in / late checkout

**New files:**

| File | Purpose |
|------|---------|
| `src/agent/__init__.py` | Exports `AgentRunner` |
| `src/agent/agent.py` | Core agent: one Claude API call per event, dispatches tool calls |
| `src/agent/tools.py` | Three tool defs: `send_cleaner_email`, `create_guest_draft`, `wait` |
| `src/agent/history.py` | `build_history_prompt(events)` — formats event log as Claude user turn |
| `src/prompts/agent_system.txt` | System prompt (editable without touching Python) |
| `src/web/hostbuddy_webhook.py` | `POST /webhook/hostbuddy` — receives HostBuddy action items |
| `tests/test_agent.py` | 4 unit tests for the agent (stubbed Anthropic client) |
| `tests/test_hostbuddy_webhook.py` | 5 route tests for the webhook |
| `Dockerfile` | python:3.11-slim, installs requirements, no CMD (overridden per service) |
| `docker-compose.yml` | Two services: `web` (port **8001**) and `daemon`; shared SQLite volume |

**Modified files:**

| File | Change |
|------|--------|
| `src/ports/memory.py` | Added `AgentEvent` dataclass + `append_event`/`get_events` abstract methods |
| `src/adapters/sqlite_memory.py` | Added `agent_events` table (auto-migrated in `__init__`), implemented new methods |
| `src/web/app.py` | Imports `AgentRunner`, builds agent in lifespan, registers `/webhook/hostbuddy` |
| `src/shell/main_cycle.py` | Guest poll loop disabled (commented); cleaner replies now routed to `agent.run()` |
| `tests/contracts/request_memory_contract.py` | 5 new event-log contract tests |

---

## Architecture decisions and why

### Trigger: HostBuddy webhooks (not Smoobu polling)

HostBuddy already parses guest messages and categorises them as action items
(`early_checkin`, `late_checkout`, etc.). Rather than poll Smoobu and re-classify
ourselves, we receive a webhook from HostBuddy with the category and a message
summary already attached. This eliminates the classification step and fires
immediately on guest message receipt.

### Prompt-first agent (not a state machine)

State machines break when edge cases appear. The agent pattern instead:
1. Appends the incoming event to a log
2. Passes the **full event log** to Claude as context
3. Claude decides what to do next via tool_use (`send_cleaner_email`, `create_guest_draft`, `wait`)
4. The agent dispatches the tool call and appends the result

This means the "state" is just the event log — no booleans to sync, no migration
needed when behaviour changes.

### Human-in-the-loop for guest reply only

The cleaner email is sent automatically. The guest reply is saved as a draft and
shown in the web review UI at `:8001` for owner approval before sending. This is
intentional for initial validation; the owner can disable the review step later.

### Single Claude call per event, not a loop

We call the Anthropic API once per event and store the result. We do not run a
tool-call loop in memory. This keeps costs low and makes the event log auditable.

### Port 8001 (not 8000)

The original repo (different project on the same server) runs on port 8000.
This repo uses **8001** to avoid conflict.

---

## End-to-end flow

```
Guest sends message in Airbnb
  └─ HostBuddy detects early_checkin or late_checkout category
       └─ POST /webhook/hostbuddy
            ├─ validates payload (Pydantic)
            ├─ idempotency check on action_item_id
            ├─ creates ProcessedRequest if none exists
            └─ agent.run(reservation_id, "hostbuddy_action_item", payload)
                 ├─ appends event to agent_events table
                 ├─ loads full event history
                 ├─ calls Claude with 3 tools (tool_choice: any)
                 └─ Claude calls send_cleaner_email
                      └─ EmailCleanerNotifier.send_query() fires
                           └─ appends cleaner_email_sent event

Email poller (daemon) finds cleaner reply
  └─ agent.run(reservation_id, "cleaner_reply", {"raw_text": "..."})
       └─ Claude calls create_guest_draft
            └─ memory.save_draft(verdict="pending")
                 └─ appends guest_draft_created event

Owner opens :8001/review
  └─ approves draft
       └─ dispatch sends reply via Smoobu API
```

---

## What's NOT done yet (next steps for Claude Code)

### 1. Run the tests

Tests were written and syntax-checked but **never executed** (no pip access in
the authoring environment).

```bash
cd /path/to/checkin-automation-main
pip install -e ".[test]"
pytest
```

Expected failures to investigate:
- `test_agent.py` — stubs use `AsyncMock`; verify the Anthropic client mock
  structure matches the real SDK response shape
- `test_hostbuddy_webhook.py` — check `app.state.memory` and `app.state.agent`
  are correctly populated in the test `lifespan` fixture
- `tests/contracts/request_memory_contract.py` — new event-log contract tests
  need to run against both `InMemoryMemory` and `SqliteMemory`

### 2. Wire up the HostBuddy webhook secret

`hostbuddy_webhook.py` currently does **no signature verification**. HostBuddy
likely signs payloads with HMAC-SHA256. Add:
- `HOSTBUDDY_WEBHOOK_SECRET` to `.env.example`
- A `verify_signature(request, secret)` helper at the top of the route

### 3. Retrieve cleaner name / property details for agent context

`agent.run()` accepts `cleaner_name`, `property_name`, `original_time`,
`requested_time` kwargs but the webhook currently passes empty strings for most
of these. Hook up a Smoobu reservation lookup to populate them from the
`booking_id` in the webhook payload.

### 4. Test with a real HostBuddy webhook

Use `ngrok` or similar to expose `:8001` and send a test action item from
HostBuddy's dashboard. Confirm the event log, cleaner email, and draft all appear
correctly.

### 5. (Optional) Remove the human-in-the-loop gate

Once the owner is confident in draft quality, remove the review step and let the
agent call `dispatch_draft()` directly. This is a one-line change in
`_handle_create_guest_draft`.

---

## Running locally

```bash
cp .env.example .env
# fill in ANTHROPIC_API_KEY, Smoobu keys, email credentials, CLEANER_EMAIL

pip install -e ".[test]"

# Run web UI + webhook server
uvicorn src.web.app:app --port 8001 --reload

# Run daemon (email poller) in a second terminal
python scripts/run.py
```

## Running with Docker

```bash
cp .env.example .env
# fill in credentials

docker compose up --build
# web UI at http://localhost:8001
```

---

## Key files to understand first

1. `src/agent/agent.py` — the heart of the feature
2. `src/prompts/agent_system.txt` — tweak this to change agent behaviour
3. `src/web/hostbuddy_webhook.py` — entry point from HostBuddy
4. `src/ports/memory.py` — the `AgentEvent` dataclass and abstract methods
5. `tests/test_agent.py` — shows expected behaviour with examples
6. `openspec/specs/hostbuddy-webhook/spec.md` — formal spec for the webhook
7. `openspec/specs/prompt-first-agent/spec.md` — formal spec for the agent

---

## Tech stack

- Python 3.11+, asyncio, FastAPI, Jinja2
- `anthropic` SDK (`claude-sonnet-4-20250514`)
- `imapclient`, `requests`, `smtplib` for email
- SQLite via `aiosqlite`
- `pytest`, `pytest-asyncio`, `httpx` for tests
- Docker + docker-compose
