# Verifying a cleaner's invoice

Monthly check that what a cleaner bills matches the turnovers that actually
happened. A **cleaning = one guest check-out**, so the source of truth is the
departure list in Beds24.

Runs `scripts/cleaning_reconcile.py` — deterministic, no LLM.

## Run it

```bash
# --month defaults to the previous calendar month:
python3 scripts/cleaning_reconcile.py ~/Downloads/F-*.pdf ~/Downloads/350-*.pdf

# explicit month:
python3 scripts/cleaning_reconcile.py --month 2026-06 ~/Downloads/F-2026-026.pdf
```

Pass every invoice PDF for the month (one per cleaner) as positional arguments.

Requirements:
- `BEDS24_READ_ALL_TOKEN` in `.env` or the environment (read-only access token).
- `pdftotext` on the PATH (poppler).
- Network access to `api.beds24.com` — run on the box or a local machine, **not**
  in the Cowork sandbox (restricted network).

## What it does

1. **Counts turnovers per property** from the Beds24 API: departures inside the
   month whose booking status is `confirmed` or `new` (`COUNTED_STATUSES`).
   Cancelled/black bookings don't count.
2. **Parses each invoice PDF** via `pdftotext`. Two known templates are
   supported: **V-Clean** and **Guilherme Veloso**. Each line item is matched to
   a property by keyword (name or address). Extra-charge lines (lit d'appoint,
   housse canapé…) are detected and skipped — they're not cleanings.
3. **Diffs** claimed quantity and the listed dates against Beds24, per property.

## Reading the output

Per invoice line, per property:

- `✓ N Beds24 turnovers` — quantity matches.
- `⚠ qty OK but dates off` — right count, but the dates listed on the invoice
  aren't the departure dates Beds24 has. Usually a typo; worth a quick look.
- `✗ Beds24 has N → billed for +/-K` — real quantity mismatch. Query with the
  cleaner.

Then a recap: **total to pay (HT)** across all invoices, and a list of issues to
raise. "All quantities match Beds24. ✓" means pay as billed.

The correctness check uses the invoice's *own* unit price; the rate in the
config table is only a reference.

## Cleaners and rates

| Cleaner | Properties | Rate / cleaning |
|---|---|---|
| **V-Clean** (Januario Lima) | Terracotta, La Palma | €35 |
| | Le Fernand | €65 |
| **Guilherme Veloso** | Le Matisse | €60 |

Velours T2 and Studio Écrin are **not yet covered** (no cleaner/rate mapped).

## Extending it

The property/cleaner/rate map is the `PROPERTIES` table at the top of
`scripts/cleaning_reconcile.py`. Each row is
`([keywords…], propertyId, name, rate)` — keywords are lowercased and
accent-stripped before matching, so include both the flat name and any address
wording the cleaner uses. Add a row for a new flat or a new cleaner's naming.

A **new invoice template** needs a new `parse_*` function alongside
`parse_vclean` / `parse_guilherme`, wired into `parse_invoice`. If a PDF yields
"could not parse any line items", that's the case you're in.
