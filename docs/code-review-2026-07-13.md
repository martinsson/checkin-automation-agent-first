# Code review — design & maintenance findings (2026-07-13)

Full-codebase review of the Python app (`src/`, `scripts/`, tests, Docker/CI).
Overall the codebase is small and clean, but it carries a half-removed old
architecture and a few things that look wired-up but aren't. Ranked by impact.

Status legend: ☐ open · ☑ done

## ☐ 1. Dead architecture that no longer imports

The repo migrated from a polling daemon to webhook-driven flow, but the old
shell was only half deleted:

- `src/shell/main_cycle.py` imports `src.ports.intent`,
  `src.ports.reservation_cache`, `src.ports.response`, `src.ports.smoobu`,
  `src.shell.pollers`, `src.shell.handlers` — **none of these modules exist**.
  The file raises `ModuleNotFoundError` on import; it only survives because
  nothing imports it.
- `Makefile` `run-daemon` runs `python scripts/run.py`, which doesn't exist.
- `src/ports/memory.py` keeps ~9 abstract methods whose only callers were the
  deleted poller (`has_message_been_seen`, `mark_message_seen`,
  `has_been_processed`, `update_status`, `get_drafts_for_request`,
  `get_reviewed_unsent_drafts`, `mark_draft_sent`, `delete_request`,
  `delete_seen_message`). Every future `RequestMemory` adapter must implement
  this dead surface; `sqlite_memory.py` dutifully does, and the contract tests
  test it.

Fix: delete the shell package, the dead Makefile target, the dead port methods
+ adapter implementations + their contract tests, and the now-orphaned
`seen_messages` table from the schema.

## ☐ 2. "Approve & Send" doesn't send

The review UI button (`src/web/routes.py`) says **Approve & Send**, but
approving only sets `verdict='ok'`. The dispatcher was
`src/shell/handlers/draft_dispatch` — one of the deleted modules — so
`sent_at` stays NULL forever. Either the button/docs should say "owner sends
manually in Airbnb", or a dispatch stage needs rebuilding.

## ☐ 3. Cleaner replies are never picked up in production

`EmailCleanerNotifier.poll_responses()` (IMAP) is only called from the dead
poll cycle and `scripts/simulate.py`. The deployed stack runs only the web
app, so the agent's "wait for the cleaner's reply" step waits forever unless
someone manually POSTs to `/webhook/cleaner-reply` — an endpoint documented as
"for testing without real IMAP". Related: `_seen_uids` is in-memory, so any
polling process would re-process every historic cleaner email after a restart.

## ☐ 4. Unauthenticated endpoints expose guest data and drive the agent

`AuthMiddleware` (`src/web/auth.py`) exempts whole path prefixes:

- `/events/{reservation_id}` and `/requests/{reservation_id}` return guest
  names, messages, and the full event log to anyone, no auth.
- `/webhook/cleaner-reply` is public and lets anyone inject a fake
  "cleaner said yes" event that produces a guest-facing draft.
- `/webhook/hostbuddy` has no signature/shared-secret verification.

Fix: static header token on the webhooks; drop the `/events/` + `/requests/`
exemptions.

## ☐ 5. XSS in the review UI

`src/web/routes.py` interpolates guest-controlled text into HTML f-strings
with no escaping (`message_summary`, `guest_name`, cleaner `raw_text`, draft
body). A guest message containing `<script>` runs in the owner's authenticated
session. `door_codes.py` escapes correctly; `routes.py` doesn't. (`jinja2` is
already a dependency, unused — it auto-escapes.)

## ☐ 6. Webhook idempotency loses events on failure

`hostbuddy_webhook.py` calls `mark_action_item_seen` **before** running the
agent. If `agent.run` throws, the request 500s but HostBuddy's retry is
rejected as a duplicate — the event is lost. Also `int(payload.booking_id)`
raises an unhandled 500 on non-numeric ids; validate in the Pydantic model.

## ☐ 7. Blocking calls inside async handlers

The app is async but the I/O is synchronous, on the event loop:

- `AgentRunner` uses the sync `anthropic.Anthropic` client — a multi-second
  Claude call blocks every other request, and the webhook response waits for
  the full agent run. (`anthropic.AsyncAnthropic` exists.)
- `sqlite_memory.py` does sync `sqlite3` in `async` methods
  (plus `check_same_thread=False` with no locking).
- `email_notifier.py` uses sync `smtplib`/`imapclient` in async methods,
  while `aiosmtplib` sits unused in requirements.txt.

## ☐ 8. Dependency and packaging hygiene

`requirements.txt`: `jinja2`, `itsdangerous`, `aiosmtplib` are unused;
`httpx`, `pyyaml`, `tzdata` are listed under `# Test` but are runtime deps
(`make_door_lock.py`, `device_map.py`, `ZoneInfo("Europe/Paris")`).
`pyproject.toml` has only pytest config — no project metadata or dependency
declaration.

## ☐ 9. Session cookie is the raw shared secret

`auth.py` docstring says "signed session cookie", but the cookie value is
literally `REVIEW_TOKEN`. Logout invalidates nothing, the secret rides on
every request for a year, rotation logs everyone out, and the username is
cosmetic. `itsdangerous` is already a dependency — sign `{user, issued_at}`
instead. Token comparisons should use `hmac.compare_digest`.

## ☐ 10. Smaller maintenance items

- Duplicated SMTP config + send logic: `contact.py`, `email_notifier.py`, and
  `app.py` each re-implement the `EMAIL_*`-or-`SMTP_*` env fallback dance.
- Hardcoded old model `claude-sonnet-4-20250514` in `agent.py`, no override.
- `sqlite_memory.py` migrations swallow every `OperationalError` — a genuinely
  failed migration passes silently. Match "duplicate column name" instead.
- `app = create_app()` at import time forces the conftest env-pre-seeding
  hack; a uvicorn `--factory` would decouple tests from import order.
- HTML built as inline f-strings in three modules with three styling
  approaches (ties into the unused-jinja2 point).
- `load_device_map` is `lru_cache`d — YAML edits need a container restart.
- `agent.py` dispatches only `tool_calls[0]`; a second tool call from Claude
  vanishes silently (no log, no `disable_parallel_tool_use`).
