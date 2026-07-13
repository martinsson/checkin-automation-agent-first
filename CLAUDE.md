# CLAUDE.md

Project working notes for agents. Keep durable, easy-to-forget facts here.

## Deployed apps (Hetzner, one box)

Two separate app deployments: **this repo** → `:8001` → `rental2.changit.fr`
(container `checkin-automation-agent-first-web-1`). A different/older `checkin-automation`
app → `:8000` → `rental.changit.fr`.

## Beds24 API access

Tokens live in **`.env`** (values there, never commit/print them). Auth model: a
long-life **refresh token** mints 24h **access tokens**; every endpoint except
`/authentication/token` needs an access token in the `token:` header. See the
`beds24-auth` skill for the full reference.

| `.env` var | What it is | Scopes | Use |
|---|---|---|---|
| `BEDS24_READ_ALL_TOKEN` | Long-life **access** token (read-only) | `read:bookings(+personal,+financial)`, **`read:inventory`**, `read:properties`, `read:accounts`, `read:channels` | Use directly as `token:` for any **read** (bookings, availability/inventory) |
| `BEDS24_REFRESH_TOKEN` | Refresh token | mints access with `read/write:bookings(+personal)`, `read/write:properties`, `read:accounts`, `read:channels` | For **writing bookings** (create/modify/delete, incl. blocks) |
| `BEDS24_ACCESS_TOKEN` | 24h access token | — | Usually stale; re-mint from the refresh token |

**No token has `write:inventory`** → availability *overrides* (stop-sell / numAvail)
can't be set via API. To open/close dates either modify the relevant **booking**
(blocks are bookings, see below) or use the Beds24 UI calendar.

```bash
# read (availability, bookings):
curl -s -H "token: $BEDS24_READ_ALL_TOKEN" \
  "https://api.beds24.com/v2/bookings?propertyId=328510&departureFrom=2026-06-22"

# write (needs a fresh access token from the refresh token):
AT=$(curl -s -H "refreshToken: $BEDS24_REFRESH_TOKEN" \
  https://api.beds24.com/v2/authentication/token | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s -H "token: $AT" -X POST https://api.beds24.com/v2/bookings -d '[{...}]'
```

The account owner is `martinsson.rouveyrol` (ownerId 162463). The Make igloohome
scenario uses a *separate*, write-only (`write:bookings`) token — don't confuse it
with the `.env` tokens above.

## Property / room IDs (Beds24)

| Property | propertyId | roomId |
|---|---|---|
| Velours T2 | 318188 | |
| Studio Écrin | 318189 | |
| Terracotta | 326123 | 676797 |
| Le Matisse | 326234 | |
| La Palma | 326275 | 677096 |
| Le Fernand \| Campus / Parking | 328510 | 681679 |

**Availability blocks** are created as bookings with `status: "black"` (ref =
owner name). To "make available" a blocked range, modify/shorten that black
booking — don't just delete it if a long-term block should remain afterward.

## Cleaning invoice reconciliation

`scripts/cleaning_reconcile.py` checks the monthly cleaner invoices against
Beds24 turnovers (a cleaning = one guest check-out). Deterministic, no LLM:
counts departures with status `confirmed`/`new` per property, parses the two
known invoice PDF templates (V-Clean / Guilherme Veloso) via `pdftotext`, and
diffs claimed quantity + listed dates. Extra-charge lines (lit d'appoint, housse
canapé) are auto-skipped.

```bash
# --month defaults to the previous calendar month:
python3 scripts/cleaning_reconcile.py ~/Downloads/F-*.pdf ~/Downloads/350-*.pdf
```

Property/cleaner/rate map is the `PROPERTIES` table at the top of the script; add
rows there for new cleaners or the not-yet-covered flats (Velours T2, Studio
Écrin). Uses `BEDS24_READ_ALL_TOKEN`. Cleaners: **V-Clean** (Januario Lima —
Terracotta/La Palma €35, Le Fernand €65) and **Guilherme Veloso** (Le Matisse €60).

## Payment reconciliation (direct bookings)

`scripts/payment_reconcile.py` checks that guests on **direct** bookings (not
Airbnb/Booking.com — those are collected by the channel) have paid by bank
transfer before arrival. Deterministic, no LLM:

1. Reads upcoming arrivals from Beds24 (`includeInvoiceItems=true`), keeps direct
   bookings with a balance due (`referer` not in `COLLECTED_CHANNELS`).
2. Reads incoming-transfer alerts from the Gmail inbox over IMAP — Banque
   Populaire (`nepasrepondre@banquepopulaire.fr`, "Suite Entreprise" / alerts) and
   Qonto (`support@qonto.com`).
3. Matches by amount (±1€) with a guest-name hint and classifies each booking:
   `PAID` / `WRONG_AMOUNT` / `UNPAID` (arrival near, nothing matched → chase).

```bash
python3 scripts/payment_reconcile.py                 # next 5 days, report only
python3 scripts/payment_reconcile.py --days 10 --email-to martinsson.johan@changit.fr
python3 scripts/payment_reconcile.py --mark-paid     # write matched payments to Beds24
```

Uses `BEDS24_READ_ALL_TOKEN` for reads and, only for `--mark-paid`, mints a
write token from `BEDS24_REFRESH_TOKEN` and posts a `payment` invoice item
(idempotent: tagged `[auto-reconcile:<sig>]`, skipped if already present). So
once a transfer is recorded, the booking's balance hits 0 and it stops being
flagged. Bank e-mail senders + collected-channel list are constants at the top of
the script. Tests: `tests/test_payment_reconcile.py` (real BP/Qonto wording).

Note: this must run where it can reach `api.beds24.com` + Gmail IMAP (the box, or
a local machine) — not the Cowork sandbox, whose network is restricted.

## Door codes / self check-in

See [docs/door-codes.md](./docs/door-codes.md) →
[docs/igloohome-make-scenario.md](./docs/igloohome-make-scenario.md). PIN
generation runs in a Make.com scenario (id 5738113), not in this repo.

**Sending an ad-hoc/early-checkin code to the guest** is designed but not built:
[docs/plans/early-checkin-code-to-guest.md](./docs/plans/early-checkin-code-to-guest.md)
(detect-if-already-sent + app-side deferred queue; two facts still open).

**Access text for tradespeople** (plumber/electrician): follow
[docs/handyman-access-text.md](./docs/handyman-access-text.md) — fetch arrival +
code messages from Smoobu or Beds24 via `scripts/access_text_fetch.py`, strip
guest-specific lines, merge, leave the PIN as `[CODE PIN]`.

## Make.com scenarios — manage via committed blueprints

Make scenarios are managed as **committed blueprints** so the repo stays the
source of truth. Blueprints live in `docs/make/*.blueprint.json`.

- **Always apply changes by importing a blueprint**, never by hand-editing
  modules in the Make UI. Edit the committed `*.blueprint.json`, then in Make:
  scenario **⋯ → Import blueprint** (imports into the open scenario, reusing its
  existing webhook + connections).
- **Before updating an existing scenario, export it first** (**⋯ → Copy blueprint
  to clipboard**, then `pbpaste`) and diff against the committed file — this
  catches edits someone made by hand in the UI. Reconcile *before* importing so
  you never silently overwrite that drift.
- Exports are safe to commit: connections/webhooks are referenced by numeric id
  (no tokens or URLs), and the clipboard export strips the `zone` JWT. Still grep
  a fresh export for `token|hook.eu|secret|api_key` before committing.
- Scheduling ("Immediately as data arrives" vs an interval) is a scenario-level
  setting, not always in the blueprint — re-check it after an import. A
  webhook→response scenario **must** be *Immediately* or the webhook returns a
  bare `Accepted` (and callers that expect JSON fail to parse it).

Known blueprints:
- `docs/make/igloohome-create-code.blueprint.json` — door-code (Igloohome
  AlgoPIN) webhook API used by `/door-codes` and the agent's `create_door_code`
  tool (scenario 6528429, eu1). Contract + setup in `docs/make/README.md`.
- `docs/make/integration-webhooks.blueprint.json` — "Integration Webhooks", the
  production Igloohome→Beds24 PIN scenario (id 5738113, eu1): webhook → set vars
  → Igloo AlgoPIN → Beds24 auth → POST booking. **The Beds24 refresh token in
  module 100's `refreshToken` header is redacted to `REDACTED_BEDS24_REFRESH_TOKEN`**
  — paste the real value (`.env` `BEDS24_REFRESH_TOKEN`) back in Make after any
  re-import; never commit it.

⚠️ This scenario type hard-codes a **Beds24 refresh token** in an HTTP header, so
always run the secret scan (above) on a fresh export and redact before committing.
