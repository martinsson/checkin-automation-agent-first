# Send an early-checkin (ad-hoc) door code to the guest

## Status: design only — not built (as of 2026-07-13)

Two facts are still needed before implementation (see **Open questions**). This
document captures the problem, the decisions already made, the chosen approach,
and the alternatives that were ruled out, so the work can be picked up later
without re-deriving it.

## Problem

When the owner creates an ad-hoc Igloohome code via `/door-codes` (typically an
**early check-in**, sometimes maintenance), the PIN is shown on screen but never
reaches the guest. We want the guest to receive that code.

The hard part is **timing**. The owner might create the early code anywhere from
a week to an hour before arrival. Meanwhile the normal self-checkin PIN is
generated and messaged automatically by a **separate** Make scenario
(id 5738113) a few hours to a day before arrival. So at the moment the owner
creates the early code, the automatic check-in message may or may not have
already gone out — and that changes what needs to happen.

## Constraints / facts established

- **Channel.** Airbnb guests have no reliable email/phone; the only dependable
  channel is the Beds24 booking message thread (which relays to Airbnb, or
  emails direct/Booking.com guests). So delivery goes through Beds24, not a
  direct email/SMS.
  - `POST /bookings/messages` sends a **literal** message on a booking — **no
    placeholder resolution**. Whatever we POST is sent verbatim, so the app
    composes the full text (and picks FR/EN itself).
  - Updating the *stored* code on a booking (`POST /bookings`, a custom
    field/infoItem) is a **different effect** — it keeps the record consistent
    but does **not** notify anyone on its own.
- **Credentials.** The `.env` `BEDS24_REFRESH_TOKEN` already mints
  `write:bookings` access tokens, so the app can both send messages and update
  bookings without new credentials. (See `CLAUDE.md` → Beds24 API access.)
- **No resident scheduler.** The intended sleep loop (`scripts/run.py`) does not
  exist; guest-message handling moved to the HostBuddy webhook; the deployed
  unit is just the uvicorn web container. Anything "fire near arrival, on a
  schedule" has **no home in the app today**. The only thing that fires on a
  schedule near arrival is the Make check-in scenario 5738113 — which is **not**
  captured as a committed blueprint and generates its own code.

## Decisions already taken (owner, 2026-07-13)

1. **Code role: the early code replaces the normal one, but coexistence is
   fine.** The early code typically spans the whole stay, so the normal
   auto-sent code is redundant — but both PINs being valid on the lock, and the
   guest possibly receiving two code messages, is acceptable. → We do **not**
   need to suppress scenario 5738113.
2. **Delivery strategy: detect whether the check-in message already went out**,
   and branch:
   - **Already sent** (e.g. created an hour before; the auto-message went out
     yesterday) → the app must actively message the guest **now** with the early
     code. Nothing else will re-fire.
   - **Not yet sent** (e.g. created a week before) → do **not** message a week
     early. The guest will get a message near arrival anyway; the early code
     should ride that timing instead of arriving uselessly early.
3. **No per-user token / per-user identity.** The login stays a single shared
   password (`johan`/`aurelia` both use `REVIEW_TOKEN`); the username exists only
   so browsers save the credential. Not relevant to this feature except that
   "who created the code" is not tracked and should not be assumed available.

## Chosen approach — M2: app-side deferred queue + cron flush

On code creation, detect via `GET /bookings/messages` whether the check-in/code
message has already been sent for that booking, then:

- **Already sent** → `POST /bookings/messages` immediately with the composed
  early-code message.
- **Not yet sent** → persist a pending notification (in the existing
  `SqliteRequestMemory`) with `send_at ≈ arrival − offset`. A tiny periodic flush
  sends it when due.

For the flush, **do not** build a resident scheduler. Add one internal endpoint
(e.g. `POST /tasks/flush-notifications`) and drive it from a **cron / systemd
timer on the Hetzner box** (curl it every 15–30 min). This reuses the existing
box + SQLite, touches no Make scenario, and is fully testable in the app.

Why M2:

- All new logic lives in code we control and can unit-test.
- No change to the un-blueprinted, self-code-generating scenario 5738113.
- The "two messages near arrival" case (early-access + normal) is acceptable per
  decision #1.

Sketch of the pieces to build:

- Door-code form gains a **booking selector** (reuse the apartment dropdown to
  fetch arrivals for the chosen property; `DoorCodeRequest.reservation_id`
  already exists on the port). A free booking-id field may be needed for edge
  cases — see open questions.
- A `pending_notifications` table in `SqliteRequestMemory`
  (booking id, composed body / code, `send_at`, sent-marker).
- "Already sent?" detector against `GET /bookings/messages` (look for the prior
  code/check-in message — confirm the marker during build).
- Message composer (FR/EN by `[GUESTLANGUAGE]`, but resolved app-side since the
  literal API does not substitute placeholders).
- `POST /tasks/flush-notifications` endpoint + a server cron/systemd timer.

## Alternatives considered and rejected

- **M1 — ride the existing scheduled Make message.** Write the early code onto
  the booking and make scenario 5738113 reuse a stored code instead of
  generating a fresh one, so its scheduled message carries the early code (one
  message, at the normal time). *Rejected:* requires reverse-engineering and
  modifying an un-blueprinted Make scenario and locating/creating the booking
  field it reads — highest-unknown, most fragile. Its only real advantage
  (exactly one message) is not required, since coexistence is acceptable.
- **M3 — always message immediately, worded as "early access."**
  Timing-independent and simplest. *Rejected* by the owner in favor of
  detection: it messages a week early and can produce a stray early code
  message far ahead of arrival.
- **Update the stored code and re-trigger Beds24's own check-in message.**
  *Rejected:* if the check-in message already went out, a field update does not
  re-send, and reliably re-firing an auto-action is finicky.

## Open questions (needed before build)

1. **Booking picker scope.** Should it list arrivals today/tomorrow for the
   chosen apartment, or also offer a free booking-id field for edge cases?
2. **Auto-send offset.** How long before arrival does the normal self-checkin
   code message currently go out (a day? a few hours?)? This sets the `send_at`
   for a deferred early-code message and sanity-checks the "already sent?"
   detection.

## Related

- `docs/make/README.md` — the create-code webhook contract (scenario 6528429).
- `src/web/door_codes.py` — the `/door-codes` form and `create_door_code` tool.
- `src/ports/door_lock.py` — `DoorCodeRequest` (already carries `reservation_id`).
- `beds24-messaging` skill — `POST /bookings/messages` semantics, placeholder
  list, booking-status filters.
- Memory: `project_igloohome_airbnb_via_beds24`, `project_igloohome_make_timing_fix`.
