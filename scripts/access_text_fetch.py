#!/usr/bin/env python3
"""Fetch the raw access/arrival material for a rental, from Smoobu OR Beds24.

This is step 1 of the "handyman access text" runbook (see
docs/handyman-access-text.md): it pulls the *raw* templates/messages so an LLM
(the agent) can strip guest-specific lines and merge them into one access text.
It does NOT do the merge — that's a judgement step done by the agent.

Why both sources matter: the arrival-instructions template alone is not enough.
The separate "code"/"lock" message often carries real entry mechanics (e.g. the
PIN is entered followed by `#`). Always merge BOTH.

Usage:
    python3 scripts/access_text_fetch.py "Hippocrate"        # auto-detect provider
    python3 scripts/access_text_fetch.py "Le Matisse" --provider beds24
    python3 scripts/access_text_fetch.py --list              # list all known units

Credentials come from .env (BEDS24_READ_ALL_TOKEN, SMOOBU_API_KEY).
"""
import argparse
import json
import os
import re
import sys
import urllib.request

BEDS24 = "https://api.beds24.com/v2"
SMOOBU = "https://login.smoobu.com/api"
# Smoobu's WAF 403s the default python-urllib User-Agent; send a normal one.
UA = "Mozilla/5.0 (access_text_fetch.py)"

# subjects (case-insensitive substrings) that identify the two message kinds
ARRIVAL_HINTS = ("arrival", "arrivée", "arrivee", "instruction", "check-in", "checkin")
CODE_HINTS = ("lock", "code", "serrure", "digicode", "pin", "clé", "cle")


def load_env(path=".env"):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get_json(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def strip_html(s):
    s = re.sub(r"<br\s*/?>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# ---------- Smoobu ----------

def smoobu_headers():
    key = os.environ.get("SMOOBU_API_KEY")
    if not key:
        sys.exit("SMOOBU_API_KEY not set in .env")
    return {"Api-Key": key, "Content-Type": "application/json", "User-Agent": UA}


def smoobu_apartments():
    return get_json(f"{SMOOBU}/apartments", smoobu_headers()).get("apartments", [])


def smoobu_fetch(apt_id):
    """Return raw arrival + code message bodies from recent reservations."""
    h = smoobu_headers()
    res = get_json(
        f"{SMOOBU}/reservations?apartmentId={apt_id}&pageSize=25&showCancellation=false",
        h,
    )
    bookings = res.get("bookings", [])
    found = {}  # kind -> (subject, body)
    for b in bookings:
        msgs = get_json(f"{SMOOBU}/reservations/{b['id']}/messages", h)
        msgs = msgs.get("messages", msgs if isinstance(msgs, list) else [])
        for m in msgs:
            subj = (m.get("subject") or "").strip()
            body = strip_html(m.get("message") or m.get("messageBody") or "")
            low = subj.lower()
            if "arrival" not in found and any(x in low for x in ARRIVAL_HINTS):
                found["arrival"] = (subj, body)
            elif "code" not in found and any(x in low for x in CODE_HINTS) and body:
                found["code"] = (subj, body)
        if "arrival" in found and "code" in found:
            break
    return found


# ---------- Beds24 ----------

def beds24_headers():
    tok = os.environ.get("BEDS24_READ_ALL_TOKEN")
    if not tok:
        sys.exit("BEDS24_READ_ALL_TOKEN not set in .env")
    return {"token": tok, "User-Agent": UA}


def beds24_properties():
    d = get_json(f"{BEDS24}/properties", beds24_headers())
    return d.get("data", [])


def beds24_fetch(prop_id):
    """Return the arrival templates (template1 FR / template2 EN)."""
    d = get_json(f"{BEDS24}/properties?id={prop_id}&includeTexts=all", beds24_headers())
    p = d["data"][0]
    t = p.get("templates", {}) or {}
    found = {}
    if t.get("template1"):
        found["arrival"] = ("template1 (FR arrival block)", t["template1"].replace("\r\n", "\n"))
    if t.get("template2"):
        found["arrival_en"] = ("template2 (EN arrival block)", t["template2"].replace("\r\n", "\n"))
    return found, p.get("name")


# ---------- driver ----------

def find_units(query):
    """Return list of (provider, id, name) matching query across both sources."""
    hits = []
    try:
        for a in smoobu_apartments():
            if query.lower() in a["name"].lower():
                hits.append(("smoobu", a["id"], a["name"]))
    except Exception as e:
        print(f"[warn] smoobu lookup failed: {e}", file=sys.stderr)
    try:
        for p in beds24_properties():
            if query.lower() in (p.get("name") or "").lower():
                hits.append(("beds24", p["id"], p["name"]))
    except Exception as e:
        print(f"[warn] beds24 lookup failed: {e}", file=sys.stderr)
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", help="apartment/property name substring")
    ap.add_argument("--provider", choices=["smoobu", "beds24"], help="force provider")
    ap.add_argument("--list", action="store_true", help="list all units and exit")
    args = ap.parse_args()
    load_env()

    if args.list:
        for a in smoobu_apartments():
            print(f"smoobu  {a['id']}  {a['name']}")
        for p in beds24_properties():
            print(f"beds24  {p['id']}  {p['name']}")
        return

    if not args.query:
        ap.error("provide a name substring, or --list")

    hits = find_units(args.query)
    if args.provider:
        hits = [h for h in hits if h[0] == args.provider]
    if not hits:
        sys.exit(f"No unit matching {args.query!r}")
    if len(hits) > 1:
        print("Multiple matches — narrow the query or pass --provider:", file=sys.stderr)
        for prov, uid, name in hits:
            print(f"  {prov}  {uid}  {name}", file=sys.stderr)
        sys.exit(1)

    provider, uid, name = hits[0]
    print(f"### {name}  ({provider} id {uid})\n")

    if provider == "smoobu":
        found = smoobu_fetch(uid)
        if not found:
            sys.exit("No arrival/code messages found in recent reservations.")
        for kind in ("arrival", "code"):
            if kind in found:
                subj, body = found[kind]
                print(f"--- {kind.upper()} MESSAGE  (subject: {subj}) ---")
                print(body)
                print()
        if "code" not in found:
            print("[note] no separate code/lock message found — check manually.", file=sys.stderr)
    else:
        found, name = beds24_fetch(uid)
        for kind in ("arrival", "arrival_en"):
            if kind in found:
                subj, body = found[kind]
                print(f"--- {subj} ---")
                print(body)
                print()
        print("[note] Beds24 igloohome codes are dynamic (sent via Make), so there is "
              "no stored code message; entry mechanics live inside the arrival block.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
