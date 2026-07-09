#!/usr/bin/env python3
"""Reconcile cleaner invoices against Beds24 turnovers.

Deterministic, no LLM. For a given month it:
  1. Counts real check-outs (turnovers = cleanings) per property from the Beds24 API.
  2. Parses each cleaner PDF invoice (V-Clean and Guilherme Veloso templates).
  3. Diffs claimed quantity/dates vs Beds24 and prints a pay-recap + a correctness check.

Usage:
  python3 scripts/cleaning_reconcile.py --month 2026-06 ~/Downloads/F-2026-026.pdf ~/Downloads/350-*.pdf

Needs BEDS24_READ_ALL_TOKEN (from .env or the environment).
"""
import argparse
import calendar
import json
import os
import re
import subprocess
import sys
import unicodedata
import urllib.request
from datetime import date

# ---------------------------------------------------------------- config ----
# Match a property from any text a cleaner might use (name or address), plus the
# reference rate we expect. Rate is informational; the check uses the invoice's
# own unit price. Add rows here as new cleaners/properties appear.
PROPERTIES = [
    # keywords (lowercased, accent-stripped)      propertyId  name           rate
    (["terracota", "terracotta", "servan"],        326123, "Terracotta",   35),
    (["la palma", "palma"],                        326275, "La Palma",     35),
    (["fernand", "campus"],                        328510, "Le Fernand",   65),
    (["matisse", "emile zola"],                    326234, "Le Matisse",   60),
    (["velours"],                                  318188, "Velours T2",   None),
    (["ecrin", "beviere"],                         318189, "Studio Ecrin", None),
]

# Booking statuses that mean a guest actually stayed (=> a cleaning happened).
COUNTED_STATUSES = {"confirmed", "new"}

# Line labels that are NOT a turnover cleaning (extra bed, sofa cover, misc) — the
# checks skip these even if they name a property.
EXTRA_KEYWORDS = ["additionnel", "appoint", "housse", "canap"]

BEDS24 = "https://api.beds24.com/v2/bookings"


def strip(s):
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def is_extra(text):
    t = strip(text)
    return any(k in t for k in EXTRA_KEYWORDS)


def match_property(text):
    if is_extra(text):
        return None, None, None
    t = strip(text)
    for keywords, pid, name, rate in PROPERTIES:
        if any(k in t for k in keywords):
            return pid, name, rate
    return None, None, None


# ------------------------------------------------------------- beds24 --------
def load_token():
    tok = os.environ.get("BEDS24_READ_ALL_TOKEN")
    if tok:
        return tok
    env = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env):
        for line in open(env):
            line = line.strip()
            if line.startswith("BEDS24_READ_ALL_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("BEDS24_READ_ALL_TOKEN not found in env or .env")


def month_bounds(month):
    y, m = map(int, month.split("-"))
    last = calendar.monthrange(y, m)[1]
    return f"{y}-{m:02d}-01", f"{y}-{m:02d}-{last:02d}"


def fetch_departures(token, pid, month):
    """Return sorted list of {day, date, guest, channel, status} real turnovers."""
    d_from, d_to = month_bounds(month)
    qs = f"?propertyId={pid}&departureFrom={d_from}&departureTo={d_to}"
    req = urllib.request.Request(BEDS24 + qs, headers={"token": token})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r).get("data", [])
    out = []
    for b in data:
        if b.get("status") not in COUNTED_STATUSES:
            continue
        dep = b.get("departure", "")
        out.append({
            "day": int(dep[8:10]),
            "date": dep,
            "guest": f"{b.get('firstName','')} {b.get('lastName','')}".strip(),
            "channel": b.get("referer", ""),
            "status": b.get("status"),
        })
    out.sort(key=lambda x: x["date"])
    return out


# --------------------------------------------------------- pdf parsing -------
def pdf_text(path):
    return subprocess.run(["pdftotext", "-layout", path, "-"],
                          capture_output=True, text=True, check=True).stdout


def parse_days(text):
    """'5, 7, 11 et 28 Juin' -> [5,7,11,28]"""
    return sorted(int(n) for n in re.findall(r"\b(\d{1,2})\b", text))


AMOUNT = r"([\d\s.,]+?)\s*€"


def money(s):
    return float(s.replace(" ", "").replace(" ", "").replace(",", ".").replace("\xa0", ""))


def parse_vclean(text):
    """V-Clean 'Abby' template -> list of line items."""
    items = []
    # Each cleaning line: '  1   ...  unité   12   35,00 €   420,00 €'
    for m in re.finditer(
        r"^\s*\d+\s+unité\s+(\d+)\s+" + AMOUNT + r"\s+" + AMOUNT + r"\s*$",
        text, re.M):
        qty = int(m.group(1))
        unit = money(m.group(2))
        gross = money(m.group(3))
        # Look at the block of text after this line up to the next item/blank run.
        tail = text[m.end():m.end() + 800]
        name_m = re.search(r"\(([^)]+)\)", tail)  # '(Studio La Palma)'
        label = name_m.group(1) if name_m else tail.strip().split("\n")[0]
        disc_m = re.search(r"-\s*" + AMOUNT, tail[:400])
        discount = money(disc_m.group(1)) if disc_m else 0.0
        dates_m = re.search(r"Prestations? r[eé]alis[eé]es? les (.+?)\.", tail)
        days = parse_days(dates_m.group(1)) if dates_m else None
        # Use the whole block (not just the parenthetical) so extra-charge lines
        # that happen to name a property still get classified as extras.
        pid, pname, _ = (None, None, None) if is_extra(tail[:400]) else match_property(label)
        items.append(dict(label=label, pid=pid, pname=pname, qty=qty, unit=unit,
                          gross=gross, discount=discount, days=days))
    return items


def parse_guilherme(text):
    """Guilherme Veloso template -> list of line items (no per-date detail).

    Descriptions wrap across several lines, so gather continuation lines (up to
    the next blank line or item) into the label before matching the property.
    """
    lines = text.split("\n")
    item_re = re.compile(r"^\s*(\d+)\s+(.+?)\s+([\d,]+)\s+([\d,]+)\s*$")
    items = []
    for i, line in enumerate(lines):
        m = item_re.match(line)
        if not m or "TOTAL" in line.upper():
            continue
        qty, label = int(m.group(1)), m.group(2)
        unit, gross = money(m.group(3)), money(m.group(4))
        j = i + 1
        while j < len(lines) and lines[j].strip() and not item_re.match(lines[j]) \
                and "TOTAL" not in lines[j].upper():
            label += " " + lines[j].strip()
            j += 1
        pid, pname, _ = match_property(label)
        items.append(dict(label=label.strip(), pid=pid, pname=pname, qty=qty,
                          unit=unit, gross=gross, discount=0.0, days=None))
    return items


def parse_invoice(path):
    text = pdf_text(path)
    if "V-Clean" in text or "vcleangrenoble" in text:
        vendor = "V-Clean (Januario Lima)"
        items = parse_vclean(text)
    elif "GUILHERME VELOSO" in text or "labelportos" in text:
        vendor = "Guilherme Veloso"
        items = parse_guilherme(text)
    else:
        vendor = "UNKNOWN vendor"
        items = []
    total_m = re.search(r"Total\s*HT\s+" + AMOUNT, text, re.I)
    total = money(total_m.group(1)) if total_m else None
    return vendor, items, total


# ------------------------------------------------------------- report --------
GREEN, RED, YEL, DIM, BOLD, END = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"


def reconcile(month, paths):
    token = load_token()
    grand_pay = 0.0
    problems = []

    for path in paths:
        vendor, items, total = parse_invoice(path)
        print(f"\n{BOLD}== {vendor}{END}  {DIM}({os.path.basename(path)}){END}")
        if not items:
            print(f"  {RED}could not parse any line items — check the PDF template{END}")
            continue
        for it in items:
            # Non-cleaning lines (sofa cover, extra bed, "additionnels") — skip the check.
            if it["pid"] is None:
                print(f"  {DIM}· skip (extra charge): {it['label']}  {it['qty']}×{it['unit']:.2f}"
                      f" = {it['gross']:.2f}€{END}")
                continue
            beds = fetch_departures(token, it["pid"], month)
            actual = len(beds)
            net = it["gross"] - it["discount"]
            disc = f"  (–{it['discount']:.0f} disc)" if it["discount"] else ""
            head = f"  {it['pname']:<12} claimed {it['qty']:>2} × {it['unit']:.0f}€ = {net:>7.2f}€{disc}"

            ok = it["qty"] == actual
            date_note = ""
            if it["days"] is not None:
                actual_days = sorted(b["day"] for b in beds)
                extra = sorted(set(it["days"]) - set(actual_days))
                missing = sorted(set(actual_days) - set(it["days"]))
                if extra:
                    date_note += f"  bill lists non-existent day(s): {extra}"
                if missing:
                    date_note += f"  turnovers not billed: {missing}"

            if ok and not date_note:
                print(f"{GREEN}{head}  ✓ {actual} Beds24 turnovers{END}")
            elif ok and date_note:
                print(f"{YEL}{head}  ⚠ qty OK ({actual}) but dates off:{date_note}{END}")
                problems.append((vendor, it["pname"], "date list wrong (no € impact)"))
            else:
                delta = (it["qty"] - actual) * it["unit"]
                print(f"{RED}{head}  ✗ Beds24 has {actual} → billed for {it['qty']-actual:+d} "
                      f"({delta:+.0f}€){END}")
                if date_note:
                    print(f"{RED}     {date_note.strip()}{END}")
                problems.append((vendor, it["pname"],
                                 f"qty {it['qty']} vs {actual} turnovers ({delta:+.0f}€)"))
        if total is not None:
            print(f"  {BOLD}Invoice total HT: {total:.2f}€{END}")
            grand_pay += total

    print(f"\n{BOLD}── Recap {month} ──{END}")
    print(f"  Total to pay (all invoices, HT): {BOLD}{grand_pay:.2f}€{END}")
    if problems:
        print(f"  {RED}{BOLD}{len(problems)} issue(s) to query with the cleaner:{END}")
        for v, p, msg in problems:
            print(f"    {RED}• {v} / {p}: {msg}{END}")
    else:
        print(f"  {GREEN}All quantities match Beds24. ✓{END}")


def prev_month():
    t = date.today()
    y, m = (t.year - 1, 12) if t.month == 1 else (t.year, t.month - 1)
    return f"{y}-{m:02d}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Reconcile cleaner invoices vs Beds24 turnovers.")
    ap.add_argument("--month", default=None,
                    help="YYYY-MM (default: previous calendar month)")
    ap.add_argument("pdfs", nargs="+", help="invoice PDF paths")
    a = ap.parse_args()
    reconcile(a.month or prev_month(), a.pdfs)
