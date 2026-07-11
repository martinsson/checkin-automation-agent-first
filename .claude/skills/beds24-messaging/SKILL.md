---
name: beds24-messaging
description: Beds24 v2 messaging architecture — API auth flow, template variable syntax, per-property template fields, what's API-manageable vs UI-only, and editing auto-actions in the control-panel browser UI. Also covers reconciling the guest-message flow with the Smoobu side (L'Hippocrate) — Smoobu template editing, placeholders, and the Beds24↔Smoobu message mapping. Use when configuring auto-action rules, writing per-property check-in/check-out blocks, migrating or reconciling templates across PMSs (Beds24/Smoobu), or sending guest messages via API.
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

### Autoaction Status filter uses a different numeric scheme than bookedit

The autoaction Trigger tab's **Status** dropdown encodes statuses differently from the bookedit dropdown. Do not mix them.

| Label | Autoaction trigger value | bookedit dropdown value |
|---|---|---|
| Cancelled | 0 | 0 |
| Confirmed | 1 | 1 |
| New | 2 | 2 |
| Request | 3 | 3 |
| Invoice Number Assigned | 4 | — |
| Invoice Number Not Assigned | 5 | — |
| Black | 6 | 4 |
| Confirmed and Invoice Number Not Assigned | 7 | — |
| Inquiry | 8 | 5 |

Pre-baked combos available in the autoaction Status dropdown: `All` (−3), `All Not Black` (−2), `All Not Cancelled` (−1), and `Confirmed and Invoice Number Not Assigned` (7). **There is no built-in "New + Confirmed" combo.**

### "New OR Confirmed but not Inquiry" cannot be expressed in a single auto-action

The Trigger tab also has a **Booking Field Include / Exclude** mechanism with a field called "Status Code" (numeric id `31` in the `bookingfieldname`/`bookingfieldnamenot` selects). It is tempting to think you could set `Status = All Not Cancelled` + `Status Code Exclude = Inquiry` to express "new + confirmed but not inquiry".

**That doesn't work.** Empirical test on 2026-05-30: "Status Code" in the Booking Field condition maps to the **secondary `statusCode`** (v2 API `statusCode` integer: 0=none, 1=Action required, 2=Allotment, 3=Cancelled by guest, 4=Cancelled by host, 5=No show, 6=Waitlist, 7=Walkin, 8=Non payment), **not** the primary booking status. Setting `bookingfieldnamenot=31, bookingfieldincludenot=0` excluded a booking with `status=new, statusCode=0`; values `1`/`2`/`8` did not exclude `new` bookings.

There is **no** Booking Field condition that targets primary booking status. To fire on both `new` and `confirmed`, clone the auto-action into two siblings (one per status). Beds24 deduplicates per-action-per-booking, so a booking that progresses `new → confirmed` fires each clone at most once. Keep bodies skinny (use `[PROPERTYTEMPLATEn]` per-property templates) to minimize drift between the clones.

The full plan and test transcript: `docs/plans/beds24-status-filter-fix.md`.

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

## Rendered-output preview without sending (booking edit page)

The Test tab gives **trigger verdicts**, not rendered output. For a full rendered preview (placeholders + IFLIKE + per-property templates all resolved), use the booking edit page:

1. Open `?ajax=bookedit&id=<bookingId>&tab=2` (Mail & Actions).
2. Find the rule's row → click **"Send Now"** (or **"Resend"** if it's already fired). Each is a `<span onclick="popupmail(<bookingId>, <ruleId>)">` that loads `?ajax=sendmail&…` into a fancybox iframe.
3. The iframe shows: To, Cc, Subject, **fully-resolved Message** (plain + HTML). All variables are substituted, IFLIKE branches resolved, per-property templates inlined. The body is editable here before sending.
4. To preview only → click **Cancel**. To send → click **Send**.

**Important: "Resend" actually sends** if you click Send in the modal — so on a real guest's booking, the modal previews fine, but don't hit the Send button unless you mean to. For safe verification without risk of accidental dispatch, create a throwaway booking via API with your own email + `allowAutoAction:disable`, preview, then cancel+delete the booking.

To read rendered output programmatically from a same-origin browser-automation context: the iframe's `[name="emailbody"]` textarea holds the HTML; the contenteditable `<div>` holds the plain-text equivalent.

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

## Reading the sent guest-message flow at scale (the reliable pull)

Auto-action *rules* aren't API-readable, but for channel (Airbnb/Booking.com) bookings the **rendered, sent messages are** — including the auto-action output, which posts into the channel thread with `source: "host"`. This is the practical way to reconstruct "what does a guest actually receive across the stay" without touching the UI. Two gotchas:

- **Do NOT use `GET /bookings?includeMessages=true`** — it returns `messages: []` even when threads exist. Empirically unreliable (verified 2026-07: 32 Matisse bookings all showed 0, yet 472 messages existed).
- **Use the dedicated `GET /bookings/messages` endpoint, paginated.** It filters by `propertyId` (or `bookingId`) and pages 100 at a time:

```bash
# All message threads for a property, paginated (page until pages.nextPageExists is false):
curl -s -H "token: $BEDS24_READ_ALL_TOKEN" \
  'https://api.beds24.com/v2/bookings/messages?propertyId=326234&maxResults=100&page=1'
```

Each message: `{bookingId, time, message, source: "host"|"guest", read}`. To isolate the **automated templates** from one-off manual chat: pull all messages, group by `bookingId`, and find the host messages whose wording recurs near-verbatim across many bookings (a template used all season shows up in N bookings; manual replies appear once). Signature-substring counting works well (e.g. count bookings containing `"Merci pour votre réservation au"` → confirmation template). `read:bookings` scope (the `BEDS24_READ_ALL_TOKEN`) is enough.

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

### Keep all four variants of a message in sync

Every auto-action carries **four** editable bodies: **FR plain, FR HTML, EN plain, EN HTML** (a subject per language too). The house rule here: **all four must say the same thing** — same wording, same variables, same placeholder slots — differing only in language and in plain-vs-HTML markup. Which one actually goes out depends on the send channel and guest language, so a stale variant ships silently:

- `Send Message = Booking API/Email Smart` posts into the **Airbnb/Booking chat thread**, and channels consume the **plain-text** body — so a correct plain body with a rotten HTML body still looks fine on Airbnb but ships garbage by email.
- Observed failure (action 568892, 2026-07): FR plain read `Bonjour [GUESTFIRSTNAME] … à 11h00`, while FR HTML had drifted to junk — `Bonjour [INVITÉPREMNOM] … à 23h00`, `Remets la clé`, `[NOM DE LA PROPRIÉTÉ]`, with `_msttexthash`/`_msthash` attributes on every node. **Root cause: the page had been saved while the browser's Microsoft page-translation (Edge/Chrome "Translate") was active** — it rewrote the on-screen HTML *and* the placeholder names (`[GUESTFIRSTNAME]`→`[INVITÉPREMNOM]`), and Summernote persisted the translated DOM into `emailbodyFR`. **Never edit/save a Beds24 message page with browser page-translation on**, and when cleaning up an already-polluted body, overwrite the HTML wholesale (don't trust a re-translate).
- **Language-specific per-property slots:** the FR body uses `[PROPERTYTEMPLATE3]`, the EN body uses `[PROPERTYTEMPLATE4]` (the flats keep FR text in slot 3, EN in slot 4). Don't reuse the same slot number across languages unless the slot itself is language-neutral.
- When the same message exists as **two actions** (e.g. one `Guests Emails` for email guests + one `Booking API` for channel guests), keep those in sync with each other too — editing one and forgetting its twin is how drift starts.

## Editing auto-actions in the control panel (browser)

Auto-action rules are UI-only. Current location (2026): **Guest Management → Auto Actions** at `https://beds24.com/control3.php?pagetype=communicationautoemails` (the old `control2.php?pagetype=…` URL redirects to the calendar). Each rule opens at `control2.php?ajax=autoemailedit&id=<id>&tab=1` (Messaging tab). The list is **account-wide** — the "Property" column shows the account name, so an edit to a shared rule hits every property.

Form (server-rendered, jQuery + Summernote):
- Subject: `emailsubEN` / `emailsubFR` (text inputs).
- Plain body: `emailtextEN` / `emailtextFR` (textareas).
- HTML body: hidden `emailbodyEN` / `emailbodyFR` textareas, driven by a Summernote editor bound to `#emailbody{LANG}_editor`; **only the active-language editor is instantiated**.

Reliable scripted edit: set the plain textareas' `.value` directly; for the **active** HTML editor use `jQuery('#emailbody{LANG}_editor').summernote('code', html)` (also set the hidden textarea); for the **inactive** language set the hidden `emailbody{LANG}` `.value` + the `_editor` div `innerHTML`.

Gotchas:
- The **Save button is often off-screen to the right** in this layout — `scrollIntoView()` / click the element, don't blind-click a fixed coordinate.
- **Save is AJAX — don't navigate the tab until it finishes** or the save aborts and silently reverts. Wait, then reload and re-read the fields to confirm persistence.
- Rendered preview (variables resolved, no send): booking edit page `?ajax=bookedit&id=<bookingId>&tab=1` → Mail & Actions → **Send Now** (click Cancel to preview only).

### The departure message is TWO auto-actions (channel + email)

Matisse's departure exists as a pair, both account-wide, that must stay identical:
- **568892 "Checkout reminder"** — `Send Message = Booking API/Email Smart` → posts into the **Airbnb/Booking chat thread** (this is what channel guests actually read). Channels use the **plain-text** body.
- **582716 "Departure - per-property extras"** — `Send Message = Guests Emails` → **email** to guests who have a real address (Airbnb guests don't, so no duplication). Uses `[PROPERTYTEMPLATE3/4]` for the per-property cot-fold line.

Editing one and forgetting its twin is the classic drift. (This is why 568892 kept shipping the stale "clé dans la boîte à clés" long after 582716 was fixed.)

## Reconciling with Smoobu (L'Hippocrate)

Le Matisse's guest messaging runs on **Beds24**; **L'Hippocrate** runs on **Smoobu** (apartment id `3230512`; Matisse is *also* in that Smoobu account as `3052591`). Both are the same six-message arc:

| Stage | Beds24 (Matisse) auto-action | Smoobu (Hippocrate) template |
|---|---|---|
| Booking confirmation | Confirmation de réservation (568630) | Booking Confirmation *(All)* |
| Access / arrival info | Arrival - access info (582702) | Arrival instructions - L'Hippocrate |
| Door / lock code | Send igloohome PIN (586256, via Make 5738113) | lock — uses `[igloohomeLockCode]` |
| First-night check-in | Mid-stay check-in (568891) | How can we make your stay better? *(All)* |
| Departure | Checkout reminder (568892, channel) + Departure extras (582716, email) | Departure info / Check-out *(All)* |
| Thank-you / review | Post-checkout thank you (568893) | Thanks for your stay! *(All)* |

Editing Smoobu templates:
- **Web UI only.** The Smoobu API can't edit templates (it exposes reservations + threads — sent copies appear in `GET /reservations/{id}/messages`, `type:2` = host, `type:1` = guest). The site needs an **interactive login** (reCAPTCHA) — the user must log in; the API key doesn't authenticate the site.
- Location: **Experience → Communication** (`/en/custom-mail`; edit at `/en/custom-mail/edit/{id}`).
- **Placeholders use [square brackets].** Key ones: `[firstGuestName]` (guest first name — NOT `[firstName]`, which is the *owner*), `[guestName]` (full name), `[igloohomeLockCode]` (door PIN; Hippocrate keypad = enter code then press the igloohome logo), `[guestAppLink]` (guest guide), `[departureTime]`, `[arrivalDate]`. Full list is in the editor's right sidebar.
- Fields: `CustomMailTranslations[0][mailSubject|mailBody]` = first language. **Add a language** via the "Add language" button → creates `CustomMailTranslations[1][BookingLanguage]` (native `<select>`) + subject/body. The first block has no language field (it's the default).
- **Save quirk:** plain server POST — a scripted `.click()` / `el.value=` submit **silently fails to persist**. Trigger a **real mouse click** on Save (it redirects to `/custom-mail` on success). Always verify by reloading the edit page and re-reading the textareas.
- **Scope trap:** templates scoped **"All" accommodations** (Booking Confirmation, stay-better, Departure, Thanks) *also* fire for **Le Matisse in Smoobu** — editing them touches Matisse too. Only Arrival-instructions + lock are per-property. (Open question worth checking: whether Matisse should be excluded from Smoobu's "All" templates to avoid duplicating its Beds24 messages.)

When reconciling, **read the live templates first** — most Hippocrate templates were already bilingual FR+EN and aligned; don't overwrite good content from a stale plan. Sent-message samples (from `/reservations/{id}/messages`) only capture whichever language went out, so they under-report existing translations.

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
