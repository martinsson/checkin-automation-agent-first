# Beds24 auto-action: trigger on `new` + `confirmed` only (exclude inquiries)

## Status: Option 1 ruled out — empirical test on 2026-05-30

**Auto-action 568889 remains on `Status = New` (the safe known-good state).**

## Problem

Beds24 trigger-status filter only offers single statuses or pre-baked combos:
`All`, `All Not Black`, `All Not Cancelled`, `Confirmed and Invoice Number Not Assigned`.
There is no built-in "New + Confirmed" combo. We want most cleaner/host auto-actions to
fire for real bookings (`new` or `confirmed`) but not for `inquiry` or `cancelled`.

## What we discovered (empirically tested 2026-05-30)

### 1. Status filter scheme (the autoaction `status` dropdown)

| Label | Trigger filter value |
|---|---|
| Cancelled | 0 |
| Confirmed | 1 |
| New | 2 |
| Request | 3 |
| Invoice Number Assigned | 4 |
| Invoice Number Not Assigned | 5 |
| Black | 6 |
| Confirmed and Invoice Number Not Assigned | 7 |
| Inquiry | 8 |

(Differs from the bookedit dropdown scheme, where Black=4 / Inquiry=5.)

### 2. The "Status Code" Booking Field condition does NOT refer to primary status

The Trigger tab exposes Booking Field Include / Exclude conditions, including a
field called **Status Code** (value `31` in the `bookingfieldname` / `bookingfieldnamenot`
dropdowns).

**Test result:** Setting `bookingfieldnamenot=31, bookingfieldincludenot=0` against
booking 87543594 (status=`new`, secondary `statusCode=0`) caused Beds24 to **exclude**
the booking. Setting the value to `1`, `2`, or `8` did NOT exclude it.

**Conclusion:** "Status Code" maps to the **secondary `statusCode`** field
(v2 API `statusCode`, the `status2` dropdown: 0=none, 1=Action required, 2=Allotment,
3=Cancelled by guest, 4=Cancelled by host, 5=No show, 6=Waitlist, 7=Walkin, 8=Non payment).

It is **NOT** the primary booking status. There is no Booking Field Include/Exclude
condition that targets the primary status (`new` / `confirmed` / `inquiry` / ...).

### 3. Other relevant control names found

In the Trigger tab, the field-condition pairs use these form input names:

| Select (field name) | Companion text input (value) |
|---|---|
| `bookingfieldname` | `bookingfieldinclude` |
| `bookingfieldname2` (AND/OR variant) | `bookingfieldinclude2` |
| `bookingfieldnamenot` (exclude) | `bookingfieldincludenot` |
| `bookingfieldnamenot2` (AND/OR exclude) | `bookingfieldincludenot2` |

Trigger Action dropdown: `enable` (0=Disable, 1=Auto, 2=Manual).

The edit form opens via `https://beds24.com/control2.php?ajax=autoemailedit&id=<actionId>&tab=<n>` —
tab 1 = Trigger, tab 8 = Test. (Auto-action settings are NOT exposed by the v2 API; UI is
the only management surface.)

## Implications

Option 1 from the original plan (Status = `All Not Cancelled` +
Status-Code field-exclude on Inquiry) is **not viable**. There is no way to express
"new OR confirmed but not inquiry" via a single auto-action's filter UI.

## Remaining options

### Option A: Live with separate single-status actions (current state)

Auto-action 568889 stays on `Status = New`. To also handle `confirmed`, clone
the action to a sibling with `Status = Confirmed`. Beds24 deduplicates per-action
per-booking, so a booking that progresses new → confirmed only fires each action once.

Tradeoff: drift risk on body edits. Mitigated by `[PROPERTYTEMPLATEn]` per-property
template pattern (skinny body, fat per-property block) so the email body in the
action itself is trivial and rarely changes.

### Option B: Meta-status combos

The only built-in combos are `All`, `All Not Black`, `All Not Cancelled`,
and `Confirmed and Invoice Number Not Assigned`. None expresses "new + confirmed,
no inquiry".

### Recommended next step

Adopt **Option A** (clone for confirmed). Document the pattern in the
`beds24-messaging` skill so future agents don't waste time re-discovering Option 1's
dead end.
