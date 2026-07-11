# CLAUDE.md

Project working notes for agents. Keep durable, easy-to-forget facts here.

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

## Door codes / self check-in

See [docs/door-codes.md](./docs/door-codes.md) →
[docs/igloohome-make-scenario.md](./docs/igloohome-make-scenario.md). PIN
generation runs in a Make.com scenario (id 5738113), not in this repo.

**Access text for tradespeople** (plumber/electrician): follow
[docs/handyman-access-text.md](./docs/handyman-access-text.md) — fetch arrival +
code messages from Smoobu or Beds24 via `scripts/access_text_fetch.py`, strip
guest-specific lines, merge, leave the PIN as `[CODE PIN]`.
