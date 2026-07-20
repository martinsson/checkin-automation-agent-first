#!/usr/bin/env python3
"""Reconcile guest bank transfers against upcoming DIRECT Beds24 bookings.

Direct bookings (not Airbnb / Booking.com — those are collected by the channel)
are paid by the guest via bank transfer, usually a few days before arrival. This
script answers, deterministically and with no LLM:

  For every direct booking arriving in the next N days, has the matching transfer
  arrived, for the right amount? If not, flag it so it can be chased.

How it works:
  1. Reads upcoming arrivals from the Beds24 API (with invoice items), keeps the
     ones whose channel is "direct" and that still owe a balance.
  2. Reads incoming-transfer alert e-mails from the Gmail inbox over IMAP
     (Banque Populaire "virement instantané reçu" notifications).
  3. Matches each unpaid booking to a credit by amount (and guest-name hint),
     inside a look-back window, and classifies it:
        PAID          - a matching credit was found
        WRONG_AMOUNT  - a credit from the guest exists but the amount is off
        UNPAID        - deadline passed and nothing matched  -> needs chasing
  4. Prints a recap. Optionally e-mails it, and optionally records the matched
     payment back on the Beds24 booking (--mark-paid) so it stops re-appearing.

Credentials come from .env:
  BEDS24_READ_ALL_TOKEN   read bookings + financial (invoice items)
  BEDS24_REFRESH_TOKEN    mint a write:bookings access token (only for --mark-paid)
  EMAIL_USER / EMAIL_PASSWORD / EMAIL_IMAP_HOST / EMAIL_IMAP_PORT   Gmail IMAP
  EMAIL_SMTP_HOST / EMAIL_SMTP_PORT   only for --email-to

Usage:
  python3 scripts/payment_reconcile.py                      # next 7 days, report only
  python3 scripts/payment_reconcile.py --days 60            # diagnostic: all upcoming
  python3 scripts/payment_reconcile.py --email-to me@changit.fr
  python3 scripts/payment_reconcile.py --mark-paid          # write matched payments to Beds24
"""
from __future__ import annotations

import argparse
import email as email_lib
import email.policy
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta

# --------------------------------------------------------------- config -----
BEDS24 = "https://api.beds24.com/v2"

# Channels whose money is collected by the platform, not by guest transfer.
# Matched case-insensitively as a substring of the booking's `referer`.
COLLECTED_CHANNELS = ["airbnb", "booking.com", "booking", "expedia", "vrbo", "abritel"]

# Booking statuses that represent a real, money-owing stay.
LIVE_STATUSES = {"confirmed", "new", "request"}

# How far back to look for a transfer relative to *today* (guests sometimes pay
# well ahead). Credits older than this are ignored.
CREDIT_LOOKBACK_DAYS = 60

# Amount match tolerance in euros (covers rounding / small fee differences).
AMOUNT_TOLERANCE = 1.00

GREEN, RED, YEL, BLU, DIM, BOLD, END = (
    "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[2m", "\033[1m", "\033[0m",
)


# --------------------------------------------------------------- helpers ----
def strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def money(s: str) -> float:
    """'4 077,00' / '16 800,00' / '1.234,56' / '324.94' -> float."""
    s = s.strip().replace("\xa0", "").replace(" ", "").replace(" ", "")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    return float(s)


def load_env_value(key: str) -> str | None:
    v = os.environ.get(key)
    if v:
        return v
    env = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def require_env(key: str) -> str:
    v = load_env_value(key)
    if not v:
        sys.exit(f"{key} not found in env or .env")
    return v


# --------------------------------------------------------------- beds24 -----
@dataclass
class Booking:
    id: int
    guest: str
    arrival: str          # YYYY-MM-DD
    referer: str
    status: str
    charges: float
    payments: float
    property_name: str = ""

    @property
    def balance(self) -> float:
        return round(self.charges - self.payments, 2)

    @property
    def is_direct(self) -> bool:
        r = strip_accents(self.referer)
        return not any(c in r for c in COLLECTED_CHANNELS)


def _get_json(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"token": token, "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _line_total(item: dict) -> float:
    """A Beds24 invoice item's signed line total."""
    amount = float(item.get("amount", 0) or 0)
    qty = item.get("qty", 1)
    try:
        qty = float(qty)
    except (TypeError, ValueError):
        qty = 1.0
    total = item.get("lineTotal")
    if total is not None:
        try:
            return float(total)
        except (TypeError, ValueError):
            pass
    return amount * qty


def fetch_direct_arrivals(token: str, days: int) -> list[Booking]:
    """Direct bookings arriving between today and today+days, with a balance due."""
    today = date.today()
    a_from = today.isoformat()
    a_to = (today + timedelta(days=days)).isoformat()
    url = (
        f"{BEDS24}/bookings?arrivalFrom={a_from}&arrivalTo={a_to}"
        f"&includeInvoiceItems=true&status=confirmed&status=new&status=request"
    )
    payload = _get_json(url, token)
    out: list[Booking] = []
    for b in payload.get("data", []):
        if b.get("status") not in LIVE_STATUSES:
            continue
        charges = payments = 0.0
        for it in b.get("invoiceItems", []) or []:
            t = (it.get("type") or "").lower()
            lt = _line_total(it)
            if t == "payment":
                payments += -lt if lt < 0 else lt  # payments may be stored negative
            else:  # charge / any non-payment line
                charges += lt
        # Fallback when no invoice items are exposed: use the booking price field.
        if charges == 0 and not (b.get("invoiceItems")):
            charges = float(b.get("price", 0) or 0)
        bk = Booking(
            id=int(b.get("id")),
            guest=f"{b.get('firstName','')} {b.get('lastName','')}".strip(),
            arrival=b.get("arrival", ""),
            referer=b.get("referer", "") or "",
            status=b.get("status", ""),
            charges=round(charges, 2),
            payments=round(payments, 2),
            property_name=b.get("propertyName", "") or str(b.get("propertyId", "")),
        )
        out.append(bk)
    out.sort(key=lambda x: x.arrival)
    return out


def mint_write_token() -> str:
    refresh = require_env("BEDS24_REFRESH_TOKEN")
    req = urllib.request.Request(
        f"{BEDS24}/authentication/token", headers={"refreshToken": refresh}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["token"]


def mark_paid(booking: "Booking", credit: "Credit", write_token: str) -> None:
    """Record the matched transfer as a payment invoice item on the booking.

    Idempotent: the description embeds the credit's signature, and we skip if a
    payment carrying that tag already exists.
    """
    tag = f"[auto-reconcile:{credit.signature()}]"
    existing = _get_json(
        f"{BEDS24}/bookings?id={booking.id}&includeInvoiceItems=true",
        load_env_value("BEDS24_READ_ALL_TOKEN") or write_token,
    ).get("data", [])
    if existing:
        for it in existing[0].get("invoiceItems", []) or []:
            if tag in (it.get("description") or ""):
                print(f"    {DIM}already recorded — skipping{END}")
                return
    body = json.dumps([{
        "id": booking.id,
        "invoiceItems": [{
            "type": "payment",
            "description": f"Virement {credit.payer} {credit.date} {tag}",
            "amount": round(credit.amount, 2),
            "qty": 1,
        }],
    }]).encode()
    req = urllib.request.Request(
        f"{BEDS24}/bookings", data=body, method="POST",
        headers={"token": write_token, "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.load(r)
        ok = (resp[0].get("success") if isinstance(resp, list) else resp.get("success"))
        print(f"    {GREEN if ok else RED}Beds24 write: {'ok' if ok else resp}{END}")
    except urllib.error.HTTPError as e:
        print(f"    {RED}Beds24 write failed: {e.code} {e.read().decode()[:200]}{END}")


# ----------------------------------------------------------------- bank -----
@dataclass
class Credit:
    amount: float
    payer: str
    reference: str
    date: str            # YYYY-MM-DD (best-effort)
    source: str          # "BP"

    def signature(self) -> str:
        return re.sub(r"[^a-z0-9]", "", strip_accents(f"{self.source}{self.date}{self.amount}{self.reference}"))[:40]


def _email_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_content()
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                return re.sub(r"<[^>]+>", " ", part.get_content())
    return msg.get_payload(decode=True).decode("utf-8", errors="replace")


def _parse_bp(subject: str, body: str, when: str) -> Credit | None:
    """Banque Populaire instant-transfer notification — RECEIVED transfers only.

    BP sends the same template for money in ("Réception d'un virement… à votre
    profit", field "Emetteur") and money out ("Exécution de votre virement…",
    field "Nom bénéficiaire"). We only ever reconcile *received* ones, so an
    outgoing/émis mail must be dropped, never matched.
    """
    text = re.sub(r"\s+", " ", f"{subject} {body}")
    flat = strip_accents(text)

    # Drop outgoing transfers outright.
    if any(k in flat for k in ("virement instantane emis", "realisation de votre ordre",
                               "nom beneficiaire", "credite sur le compte de votre beneficiaire",
                               "execution de votre virement")):
        return None
    # Require a positive "received" marker (per the bank's own wording).
    if not any(k in flat for k in ("reception d", "virement instantane recu", "a votre profit",
                                   "credite sur votre compte")):
        return None

    amt = re.search(r"Montant\s*:\s*([\d\s.,]+)", text, re.I) or \
        re.search(r"\bde\s+([\d\s.,]+?)(?:\s|$)", subject)
    if not amt:
        return None
    payer = re.search(r"Emetteur\s*:\s*(.+?)\s+(?:Montant|Compte|R[ée]f[ée]rence)\s*:", text, re.I)
    ref = re.search(r"R[ée]f[ée]rence\s*:\s*(.+?)\s+(?:Motif|Nature|Compte|Le montant|$)", text, re.I)
    dm = re.search(r"\ble\s+(\d{2}/\d{2}/\d{2,4})", text)
    d = when
    if dm:
        dd, mm, yy = dm.group(1).split("/")
        yy = yy if len(yy) == 4 else f"20{yy}"
        d = f"{yy}-{mm}-{dd}"
    reference = ref.group(1).strip() if ref else ""
    return Credit(money(amt.group(1)), (payer.group(1).strip() if payer else ""), reference, d, "BP")


def fetch_bank_credits(lookback_days: int) -> list[Credit]:
    """Read incoming-transfer alerts from the inbox.

    Alerts may arrive *forwarded* from another mailbox, so the IMAP envelope
    sender is the forwarder, not the bank. We therefore search by content
    (subject/body markers), never by the From header.
    """
    import imapclient  # local import: only needed here, already a project dep

    user = require_env("EMAIL_USER")
    pw = require_env("EMAIL_PASSWORD")
    host = load_env_value("EMAIL_IMAP_HOST") or "imap.gmail.com"
    port = int(load_env_value("EMAIL_IMAP_PORT") or 993)
    since = date.today() - timedelta(days=lookback_days)

    credits: list[Credit] = []
    with imapclient.IMAPClient(host, port=port, ssl=True) as client:
        client.login(user, pw)
        client.select_folder("INBOX", readonly=True)
        uids: set[int] = set()
        for crit in (["SUBJECT", "virement"], ["TEXT", "bpaura"], ["TEXT", "banquepopulaire"]):
            try:
                uids |= set(client.search(["SINCE", since] + crit))
            except Exception:
                pass
        if not uids:
            return []
        for uid, data in client.fetch(list(uids), ["ENVELOPE", "RFC822"]).items():
            msg = email_lib.message_from_bytes(data[b"RFC822"], policy=email_lib.policy.default)
            subject = str(msg.get("Subject", ""))
            when = date.today().isoformat()
            try:
                when = email_lib.utils.parsedate_to_datetime(msg.get("Date")).date().isoformat()
            except Exception:
                pass
            body = _email_body(msg)
            flat = strip_accents(f"{subject} {body}")
            c = None
            if "bpaura" in flat or "banque populaire" in flat or "banquepopulaire" in flat:
                c = _parse_bp(subject, body, when)     # returns None for outgoing/émis
            if c and c.amount > 0:
                credits.append(c)
    credits.sort(key=lambda x: x.date)
    return credits


# ------------------------------------------------------------- matching -----
@dataclass
class Result:
    booking: Booking
    status: str                      # PAID | WRONG_AMOUNT | UNPAID
    credit: Credit | None = None
    note: str = ""


def _name_hint(booking: Booking, credit: Credit) -> bool:
    """Does the credit's payer/reference plausibly name the guest?"""
    guest = strip_accents(booking.guest)
    hay = strip_accents(f"{credit.payer} {credit.reference}")
    tokens = [t for t in re.split(r"\s+", guest) if len(t) >= 3]
    return any(t in hay for t in tokens)


def reconcile(bookings: list[Booking], credits: list[Credit], alert_days: int) -> list[Result]:
    today = date.today()
    used: set[int] = set()
    results: list[Result] = []
    for bk in bookings:
        if not bk.is_direct or bk.balance <= AMOUNT_TOLERANCE:
            continue
        target = bk.balance
        best_i = best = None
        # First pass: amount match (prefer one that also names the guest).
        for i, c in enumerate(credits):
            if i in used:
                continue
            if abs(c.amount - target) <= AMOUNT_TOLERANCE:
                if best is None or _name_hint(bk, c):
                    best_i, best = i, c
                    if _name_hint(bk, c):
                        break
        if best is not None:
            used.add(best_i)
            results.append(Result(bk, "PAID", best))
            continue
        # Second pass: a credit that names the guest but with the wrong amount.
        wrong = None
        for i, c in enumerate(credits):
            if i in used:
                continue
            if _name_hint(bk, c):
                wrong = (i, c)
                break
        if wrong:
            used.add(wrong[0])
            delta = wrong[1].amount - target
            results.append(Result(bk, "WRONG_AMOUNT", wrong[1],
                                   note=f"reçu {wrong[1].amount:.2f}€ vs attendu {target:.2f}€ ({delta:+.2f}€)"))
            continue
        days_to = (datetime.fromisoformat(bk.arrival).date() - today).days if bk.arrival else 999
        note = f"arrivée dans {days_to} j" if days_to >= 0 else "arrivée passée"
        results.append(Result(bk, "UNPAID", None, note=note))
    return results


# --------------------------------------------------------------- report -----
def render(results: list[Result], days: int) -> str:
    lines = [f"{BOLD}Réconciliation paiements — réservations directes, arrivée ≤ {days} j{END}",
             f"{DIM}(règle : paiement dû au plus tard {days} j avant l'arrivée){END}", ""]
    paid = [r for r in results if r.status == "PAID"]
    wrong = [r for r in results if r.status == "WRONG_AMOUNT"]
    unpaid = [r for r in results if r.status == "UNPAID"]

    if unpaid:
        lines.append(f"{RED}{BOLD}❌ IMPAYÉS ({len(unpaid)}) — à relancer{END}")
        for r in unpaid:
            lines.append(f"{RED}  • {r.booking.guest:<24} {r.booking.arrival}  "
                         f"{r.booking.balance:>8.2f}€  {r.booking.property_name}  ({r.note}){END}")
        lines.append("")
    if wrong:
        lines.append(f"{YEL}{BOLD}⚠ MONTANT INCORRECT ({len(wrong)}){END}")
        for r in wrong:
            lines.append(f"{YEL}  • {r.booking.guest:<24} {r.booking.arrival}  {r.note}  "
                         f"[virement de {r.credit.payer}]{END}")
        lines.append("")
    if paid:
        lines.append(f"{GREEN}{BOLD}✅ PAYÉS ({len(paid)}){END}")
        for r in paid:
            lines.append(f"{GREEN}  • {r.booking.guest:<24} {r.booking.arrival}  "
                         f"{r.booking.balance:>8.2f}€  ← {r.credit.payer} le {r.credit.date}{END}")
        lines.append("")
    if not results:
        lines.append(f"{GREEN}Aucune réservation directe avec solde dû dans la fenêtre. ✓{END}")
    return "\n".join(lines)


def send_email(to_addr: str, subject: str, body_plain: str) -> None:
    import smtplib
    from email.mime.text import MIMEText

    user = require_env("EMAIL_USER")
    pw = require_env("EMAIL_PASSWORD")
    host = load_env_value("EMAIL_SMTP_HOST") or "smtp.gmail.com"
    port = int(load_env_value("EMAIL_SMTP_PORT") or 587)
    msg = MIMEText(body_plain, "plain", "utf-8")
    msg["From"], msg["To"], msg["Subject"] = user, to_addr, subject
    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(user, pw)
        s.sendmail(user, to_addr, msg.as_string())


def strip_ansi(s: str) -> str:
    return re.sub(r"\033\[[0-9;]*m", "", s)


# ----------------------------------------------------------------- main -----
def main() -> None:
    ap = argparse.ArgumentParser(description="Reconcile guest transfers vs direct Beds24 bookings.")
    ap.add_argument("--days", type=int, default=7,
                    help="arrival look-ahead = payment deadline window (default 7: "
                         "payment is due 7 days before arrival)")
    ap.add_argument("--lookback", type=int, default=CREDIT_LOOKBACK_DAYS,
                    help="how far back to read bank credits (default 60)")
    ap.add_argument("--email-to", default=None, help="also send the recap to this address")
    ap.add_argument("--mark-paid", action="store_true",
                    help="record matched payments back on the Beds24 booking")
    a = ap.parse_args()

    read_token = require_env("BEDS24_READ_ALL_TOKEN")
    bookings = fetch_direct_arrivals(read_token, a.days)
    credits = fetch_bank_credits(a.lookback)
    results = reconcile(bookings, credits, a.days)

    report = render(results, a.days)
    print(report)

    if a.mark_paid:
        matched = [r for r in results if r.status == "PAID"]
        if matched:
            print(f"\n{BOLD}Marquage payé dans Beds24 ({len(matched)})…{END}")
            wt = mint_write_token()
            for r in matched:
                print(f"  {r.booking.guest} / booking {r.booking.id}")
                mark_paid(r.booking, r.credit, wt)

    if a.email_to:
        problems = sum(1 for r in results if r.status in ("UNPAID", "WRONG_AMOUNT"))
        subject = (f"[Paiements] {problems} à traiter" if problems
                   else "[Paiements] tout est à jour")
        send_email(a.email_to, subject, strip_ansi(report))
        print(f"\n{DIM}Recap envoyé à {a.email_to}{END}")


if __name__ == "__main__":
    main()
