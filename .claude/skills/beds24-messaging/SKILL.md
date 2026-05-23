---
name: beds24-messaging
description: Beds24 v2 messaging architecture — API auth flow, template variable syntax, per-property template fields, and what's API-manageable vs UI-only. Use when configuring auto-action rules, writing per-property check-in/check-out blocks, migrating templates from another PMS, or sending guest messages via API.
---

# Beds24 v2 Messaging

## API vs UI — what's where

| Capability | API | UI only |
|---|---|---|
| Read/write per-property template slots (`template1`–`template8`) | ✅ `POST /properties` | — |
| Read/write property data (address, email, check-in/out times) | ✅ `POST /properties` | — |
| Send literal messages on a booking | ✅ `POST /bookings/messages` | — |
| Read message threads | ✅ `GET /bookings/messages` | — |
| **Auto-action rules** (the things that fire on booking events and resolve placeholders) | ❌ | ✅ `?pagetype=communicationautoemails` |
| **Available placeholder list** | ❌ | ✅ Rule editor sidebar |

`POST /bookings/messages` does **not** resolve placeholders — whatever you POST is sent verbatim. Placeholder substitution only happens inside auto-action rules executed by Beds24's UI-driven engine.

## Auth flow (one-time bootstrap)

1. Generate an **invite code** in the control panel (Settings → Account Access → API → "Generate invite code"). Pick scopes (`read:bookings`, `write:bookings`, `read:properties`, `write:properties`, `read:accounts`, `read:channels`, `write:channels` is a sensible default), property access, expiry.
2. Exchange it once — invite codes are single-use:
   ```bash
   curl -H "code: $INVITE_CODE" -H "deviceName: my-tool" \
        https://api.beds24.com/v2/authentication/setup
   ```
   Response: `{token, expiresIn: 86400, refreshToken}`. Save the refresh token to `.env` immediately — it's long-life and not re-derivable.
3. Refresh access tokens (every 24h) via `GET /authentication/token` with `refreshToken` header.
4. All subsequent calls use `token: <accessToken>` header.

## Placeholder syntax

Bracketed UPPERCASE: `[GUESTFIRSTNAME]`. Full list (516 tokens): https://wiki.beds24.com/index.php?title=Template_Variables

Key ones for guest messaging:

| Want | Token |
|---|---|
| Guest first name | `[GUESTFIRSTNAME]` |
| Guest full name (single token) | `[GUESTFULLNAME]` |
| Guest email | `[GUESTEMAIL]` |
| Guest language (code only, e.g. `fr`) | `[GUESTLANGUAGE]` |
| Property name | `[PROPERTYNAME]` |
| Property contact (host) email | `[PROPERTYEMAIL]` |
| Property phone | `[PROPERTYPHONE]` |
| Check-in date / time / end | `[FIRSTNIGHT]` / `[CHECKINSTART]` / `[CHECKINEND]` |
| Check-out date / time | `[LEAVINGDAY]` / `[CHECKOUTEND]` |
| Number of nights / adults / children | `[NUMNIGHT]` / `[NUMADULT]` / `[NUMCHILD]` |
| Booking ID | `[BOOKID]` |
| Total price | `[PRICE]` |
| Guest portal link | `[GUESTLOGIN]` |
| Channel/source | `[REFERRER]` |
| Per-property text slot N (1–8) | `[PROPERTYTEMPLATEn]` (plain) or `[PROPERTYTEMPLATEnBR]` (HTML — line breaks become `<br>`) |
| Per-room text slot N | `[ROOMTEMPLATEn]` / `[ROOMTEMPLATEnBR]` |

## Booking status semantics (the `new` vs `confirmed` vs `New (Confirmed)` trap)

Beds24 stores 6 status codes. API string → numeric code → bookedit dropdown label:

| API | code | UI label | Meaning |
|---|---|---|---|
| `inquiry` | 5 | Inquiry | Question only — **does not block** the room |
| `request` | 3 | Request | On-request — blocks until host accepts |
| `new` | 2 | New | Confirmed reservation, **not yet opened by host** |
| `confirmed` | 1 | New (Confirmed) / Confirmed | Same reservation, host has opened+saved it |
| `cancelled` | 0 | Cancelled | — |
| `black` | 4 | Black | Manual room block, no guest |

**`new` → `confirmed` is purely a "have you handled it yet" workflow flag**, not a payment state. Per the wiki ([Setting/bookingsstatus](https://wiki.beds24.com/index.php?title=Setting/bookingsstatus)): *"New are bookings which have not been opened to view. Once you open them and save or update them the status will change to Confirmed."* The booking is just as real either way.

**"New (Confirmed)"** is the *UI label* the booking-edit dropdown sometimes uses for code 1. Read it as two perspectives: "**New**" (Beds24's status — you haven't handled it) + "**(Confirmed)**" (the channel's view — Airbnb has confirmed it with the guest). Not a separate status — same underlying code 1.

**Implications for auto-action filters:**
- Filter `Confirmed` (code 1) → matches only bookings you've already opened. Most fresh Airbnb arrivals **won't match** until you click through them.
- Filter `New` (code 2) → matches fresh, unhandled bookings. **Use this for "fire as soon as the booking lands" rules.**
- If an upstream auto-action sets status `new → confirmed` before your rule evaluates, your `New` filter will miss it. Clone the rule with filter `Confirmed` as a belt-and-braces second path.

The Test tab labels make this debuggable: a filter mismatch shows as *"FAIL — booking has wrong status"*.

## Modifying bookings via API

- **Updates use POST, not PATCH.** `PATCH /bookings` returns HTTP 500 "Could not process request". Use `POST /bookings` with `[{"id": <bookingId>, ...fields}]`.
- **Deletion requires cancellation first.** `DELETE /bookings?id=<id>` on an active booking returns *"cannot delete active bookings"*. Two-step:
  ```bash
  curl -X POST  -H "token: $TOKEN" -H "Content-Type: application/json" \
       -d '[{"id":N,"status":"cancelled","allowAutoAction":"disable"}]' \
       https://api.beds24.com/v2/bookings
  curl -X DELETE -H "token: $TOKEN" \
       "https://api.beds24.com/v2/bookings?id=N"
  ```
  Setting `allowAutoAction:"disable"` in the same PATCH prevents the cancellation event from firing any cancellation auto-actions before the delete lands.

## `allowAutoAction` — the per-booking opt-out

Each booking has an `allowAutoAction` field (`"enable"` / `"disable"`). When `"disable"`, **no auto-actions fire for that booking**, regardless of any rule's status/source/time filters. The auto-action Test tab surfaces this as *"FAIL — booking does not allow auto actions"*.

Common gotchas:
- API-created test bookings often get `"disable"` set intentionally to avoid real emails — then later get forgotten as the cause of "why doesn't my rule fire?"
- The Beds24 booking edit UI has a checkbox "Allow Auto Actions" that toggles this; unchecking it silently disables all messaging on that booking.
- To enable: `POST /bookings` with `[{"id":N,"allowAutoAction":"enable"}]`.

## Auto-action Test tab — the cleanest debugging surface

Auto-actions can't be exercised via API, but the UI has a **Test tab** (`?ajax=autoemailedit&id=<actionId>&tab=8`) with two tools:

1. **"View bookings"** — lists every booking currently inside the rule's time/property scope (does *not* apply the status filter — that's evaluated per-booking).
2. **Per-booking test** — enter a booking ID, get a deterministic verdict:
   - ✅ `Not yet triggered` — matches all filters, scheduled to fire
   - ❌ `FAIL — booking has wrong status` — status filter mismatch
   - ❌ `FAIL — booking does not allow auto actions` — `allowAutoAction:disable`
   - ❌ `FAIL — booking does not meet trigger condition` + `End Event Time Window` — the trigger's time window has expired (booking too old for the Booking event, or arrival too far/close)
   - ❌ Other filter mismatches each get specific messages

The Test tab evaluates against the **currently saved** auto-action config (so save changes before testing) and the **current** booking state, with the message **"Local time now ..."** anchoring the evaluation.

## Conditional logic (`IF=` family)

Beds24 supports inline conditionals — a family of comparison variables that resolve other variables inside their arguments:

```
[IF=:if_this:equals_this:then_text|else_text]      # equality (case-sensitive)
[IFLIKE:if_this:is_like_this:then_text|else_text]  # case-insensitive, trims spaces
[IFIN:needle:haystack:then_text|else_text]         # substring contains
[IF>:val:threshold:then|else]                      # also IF>=, IF<, IF<=
[IFBETWEEN:val:min:max:in_range|too_low|too_high]
```

Use the `^`/`~` separator variants (`[IF=^a^b^same~different]`) when your data contains `:` or `|` — and as the **inner** IF when nesting (the `^` form is processed first).

**Language branching example:**
```
[IF=:[GUESTLANGUAGE]:fr:Bonjour|Hello] [GUESTFIRSTNAME]
```

**Design tradeoff for multilingual messages:**
- *Inline IF approach*: 1 rule per message type, body has `[IF=:[GUESTLANGUAGE]:fr:…|…]` wrappers. Compact rule count, but multi-paragraph branching becomes a giant nested string — hard to read/edit.
- *One-rule-per-language* (with a language filter on the rule's trigger): 2× rules per message type, but each body is single-language and trivially editable. Recommended for anything beyond short snippets.

## Recursion / nesting of variables

Variables inside template strings **are resolved during rendering** (recursion works). The wiki confirms this for `IF=` arguments and for templates referencing other templates:

> "Property Template1 could pull the calculation of [ROOMTEMPLATE6]"

**Direction matters:** Room Templates can be referenced inside Property Templates, **not the reverse**. Rough hierarchy: booking/guest variables (most specific) → room templates → property templates → account templates. A template can reference variables at the same or more-specific level, not less-specific.

Practical implication for the per-property injection pattern: putting `[GUESTFIRSTNAME]`, `[CHECKOUTEND]`, or `[IF=:…]` inside a property's `template1` works — those are booking-level and resolve at message-render time.

## Per-property text injection (the DRY pattern)

When the same message has property-specific paragraphs (door codes, building entry, etc.), don't duplicate the rule per property. Instead:

1. Store the property-specific paragraph in a free `templateN` slot on each property:
   ```bash
   curl -X POST -H "token: $TOKEN" -H "Content-Type: application/json" \
     -d '[{"id":326234,"templates":{"template1":"Door code 2838…"}}]' \
     https://api.beds24.com/v2/properties
   ```
   UTF-8 / accents preserved. Empty string = unset. 8 slots per property.
2. In the auto-action rule body, reference `[PROPERTYTEMPLATE1]` (or `[PROPERTYTEMPLATE1BR]` for HTML).
3. One rule body, N properties, no duplication. Convention: keep the same slot number for the same purpose across all properties (e.g. always TEMPLATE1 = arrival block, TEMPLATE2 = departure block).

## Verification recipe

Auto-action rules can't be exercised directly via API. Verified path that works:

1. Write the per-property block via `POST /properties` (template1, etc.).
2. Create a test booking via `POST /bookings` with `apiSource="Direct"` (default for API-created bookings).
3. Scope the rule with `apisource=0` (Direct) so it only fires for test bookings, not real channel bookings.
4. Use `sendtoguest=5` (Guests Emails) so the rule actually delivers — `sendtoguest=4` (Booking API/Email Smart) only fires when there's a channel API integration (Airbnb, Booking.com), and silently stays "pending" forever for Direct bookings.
5. Open the booking edit page at `https://beds24.com/control2.php?ajax=bookedit&id=<bookingId>&tab=1` → Mail & Actions tab → click **Send Now** on the rule. This renders + delivers immediately.
6. Verify: change the booking's `email` to a real inbox you control, OR use `apiSource=Airbnb`/`Booking.com` so the rendered message lands in `/bookings/messages` (only channel-mediated chats appear there).

The Beds24 messages API endpoint `GET /bookings/messages` only returns **channel chat threads** (Airbnb/Booking.com), not standard email sends. Don't expect Direct booking emails to show up there.

## HTML formatting rules

Beds24 stores TWO bodies per language (`emailtextFR` plain + `emailbodyFR` HTML, same for EN). If you only fill the plain version:
- Plain text email goes out fine (newlines preserved).
- HTML email is auto-derived from plain — but newlines collapse into one wall of text and `[PROPERTYTEMPLATE1]` (no BR) inside it doesn't preserve its own line breaks either.

**Always fill both** when content has multi-line structure:
- `emailtextFR`: plain text, use non-BR placeholders (`[PROPERTYTEMPLATE1]`).
- `emailbodyFR`: explicit HTML with `<p>` tags around paragraphs, `<br>` for soft breaks, **and `[PROPERTYTEMPLATE1BR]`** (BR variant converts the template's `\n` to `<br>`).

Example:
```
emailtextFR = Bonjour [GUESTFIRSTNAME],\n\n[PROPERTYTEMPLATE1]\n\n...
emailbodyFR = <p>Bonjour [GUESTFIRSTNAME],</p><p>[PROPERTYTEMPLATE1BR]</p><p>...</p>
```

## Useful API calls

```bash
# Token in .env, sourced before each session:
TOKEN="$BEDS24_ACCESS_TOKEN"

# List all properties (id + name)
curl -H "token: $TOKEN" 'https://api.beds24.com/v2/properties'

# Read a property's template slots
curl -H "token: $TOKEN" \
  'https://api.beds24.com/v2/properties?id=326234&includeTemplates=true'

# Send a literal message on a booking (no placeholder rendering)
curl -X POST -H "token: $TOKEN" -H "Content-Type: application/json" \
  -d '[{"bookingId":1234567,"message":"Hi"}]' \
  'https://api.beds24.com/v2/bookings/messages'

# Read messages for a booking
curl -H "token: $TOKEN" \
  'https://api.beds24.com/v2/bookings/messages?bookingId=1234567'
```

OpenAPI spec: `https://api.beds24.com/v2/apiV2.yaml` (no auth required).
