---
name: cleaning-invoice-check
description: Verify a cleaner's monthly invoice against the real turnovers (check-outs) in Beds24 — running scripts/cleaning_reconcile.py, reading its output, cleaner/property rates, and adding a new property or invoice template. Use when a cleaning invoice arrives, when asked whether a cleaner billed the right number of cleanings, or when the reconcile script fails to parse a PDF.
---

# Cleaning invoice check

A cleaning = one guest check-out, so the truth is the Beds24 departure list.
`scripts/cleaning_reconcile.py` counts those and diffs them against the invoice
PDFs — deterministic, no LLM.

```bash
# --month defaults to the previous calendar month:
python3 scripts/cleaning_reconcile.py ~/Downloads/F-*.pdf ~/Downloads/350-*.pdf
```

Needs `BEDS24_READ_ALL_TOKEN`, `pdftotext`, and network access to
`api.beds24.com` (the box or a local machine — not the Cowork sandbox).

Full runbook — output legend (✓ / ⚠ dates off / ✗ mismatch), cleaner rates, and
how to add a property or a new invoice template: see
[docs/cleaning-invoice-reconciliation.md](../../../docs/cleaning-invoice-reconciliation.md).
