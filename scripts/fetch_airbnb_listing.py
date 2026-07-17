#!/usr/bin/env python3
"""Scrape an Airbnb room page into a Cocon `listings.json` entry.

The Cowork sandbox can't reach airbnb.com (network policy blocks it), so run this
where the network is open — the Hetzner box or a local machine — then paste the
printed JSON back to the agent (or use --write to patch listings.json in place).

Usage:
    python3 scripts/fetch_airbnb_listing.py \
        --url "https://www.airbnb.com/rooms/1645657560952524041" \
        --slug hippocrate --name "L'Hippocrate" \
        --channel smoobu \
        --booking-url "https://login.smoobu.com/fr/booking-tool/iframe/1547006/3230512" \
        --smoobu-apartment-id 3230512

Add --write to merge the result into site-cocon/src/data/listings.json (matches
on --slug; keeps draft=true unless --publish is given). Photos/description/geo/
capacity are best-effort scrapes off the embedded JSON + OpenGraph tags; always
eyeball them before publishing.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import urllib.request
import zlib
from pathlib import Path

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


def fetch(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        enc = resp.headers.get("Content-Encoding", "")
    if enc == "gzip":
        raw = gzip.decompress(raw)
    elif enc == "deflate":
        raw = zlib.decompress(raw)
    return raw.decode("utf-8", "replace")


def _meta(html: str, prop: str) -> str | None:
    m = re.search(
        rf'<meta[^>]+property=["\']{re.escape(prop)}["\'][^>]+content=["\'](.*?)["\']',
        html,
        re.I | re.S,
    )
    if not m:
        m = re.search(
            rf'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']{re.escape(prop)}["\']',
            html,
            re.I | re.S,
        )
    return _unescape(m.group(1)) if m else None


def _unescape(s: str) -> str:
    # JSON unicode escapes + common HTML entities, enough for prose fields.
    try:
        s = json.loads(f'"{s}"') if "\\u" in s and '"' not in s else s
    except Exception:
        pass
    return (
        s.replace("\\u2019", "’")
        .replace("&#39;", "'")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#x27;", "'")
        .strip()
    )


def extract_photos(html: str) -> list[str]:
    """All listing photos, original quality, in first-seen order."""
    urls = re.findall(r'https://a0\.muscache\.com/im/pictures/[^"\\\s?]+', html)
    seen: dict[str, None] = {}
    for u in urls:
        # keep only real listing photos (hosting/miso/prohost), drop icons/maps
        if not re.search(r"/(hosting|miso|prohost-api|monet)/", u):
            continue
        u = re.sub(r"/(im/pictures/[^/]+/[^/]+)/[^/]+/", r"/\1/original/", u)
        seen.setdefault(u, None)
    return list(seen)


def extract_description(html: str) -> str:
    m = re.search(r'"htmlText"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
    if m:
        txt = json.loads(f'"{m.group(1)}"')
        txt = re.sub(r"<br\s*/?>", "\n", txt)
        txt = re.sub(r"<[^>]+>", "", txt)
        if len(txt.strip()) > 40:
            return txt.strip()
    return _meta(html, "og:description") or ""


def first_int(html: str, *keys: str) -> int | None:
    for k in keys:
        m = re.search(rf'"{k}"\s*:\s*(\d+)', html)
        if m:
            return int(m.group(1))
    return None


def first_float(html: str, *keys: str) -> float | None:
    for k in keys:
        m = re.search(rf'"{k}"\s*:\s*(-?\d+\.\d+)', html)
        if m:
            return float(m.group(1))
    return None


def build_entry(args, html: str) -> dict:
    photos = extract_photos(html)
    title = _meta(html, "og:title") or args.name
    entry = {
        "slug": args.slug,
        "name": args.name,
        "fullName": args.name,
        "channel": args.channel,
        "airbnbId": args.url.rstrip("/").split("/")[-1].split("?")[0],
        "airbnbUrl": args.url.split("?")[0],
        "bookingUrl": args.booking_url,
        "city": args.city or "",
        "address": args.address or "",
        "postcode": args.postcode or "",
        "lat": first_float(html, "lat", "latitude"),
        "lng": first_float(html, "lng", "longitude"),
        "maxPeople": first_int(html, "personCapacity", "guestCapacity") or args.max_people,
        "maxAdult": None,
        "roomSize": None,
        "rackRate": 0,
        "minPrice": 0,
        "minStay": None,
        "checkIn": args.check_in,
        "checkOut": args.check_out,
        "description": extract_description(html),
        "photos": photos,
    }
    if args.channel == "smoobu" and args.smoobu_apartment_id:
        entry["smoobuApartmentId"] = int(args.smoobu_apartment_id)
    entry["_scraped_meta_title"] = title
    return entry


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True, help="Airbnb room URL")
    p.add_argument("--slug", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--channel", default="beds24", choices=["beds24", "smoobu"])
    p.add_argument("--booking-url", default="", help="Beds24 or Smoobu booking iframe URL")
    p.add_argument("--smoobu-apartment-id", default="")
    p.add_argument("--city", default="Grenoble")
    p.add_argument("--address", default="")
    p.add_argument("--postcode", default="")
    p.add_argument("--max-people", type=int, default=2)
    p.add_argument("--check-in", default="16:00")
    p.add_argument("--check-out", default="11:00")
    p.add_argument("--write", action="store_true", help="merge into site-cocon/src/data/listings.json")
    p.add_argument("--publish", action="store_true", help="with --write, set draft=false")
    args = p.parse_args()

    html = fetch(args.url)
    entry = build_entry(args, html)

    n = len(entry["photos"])
    print(f"# scraped {n} photos, description {len(entry['description'])} chars, "
          f"maxPeople={entry['maxPeople']}, lat/lng={entry['lat']},{entry['lng']}",
          file=sys.stderr)
    if n == 0 or not entry["description"]:
        print("# WARNING: photos or description empty — Airbnb markup may have changed; "
              "check the entry before publishing.", file=sys.stderr)

    if not args.write:
        print(json.dumps(entry, ensure_ascii=False, indent=1))
        return 0

    path = Path(__file__).resolve().parent.parent / "site-cocon/src/data/listings.json"
    listings = json.loads(path.read_text("utf-8"))
    entry.pop("_scraped_meta_title", None)
    entry["draft"] = not args.publish
    for i, l in enumerate(listings):
        if l.get("slug") == args.slug:
            listings[i] = {**l, **entry}
            break
    else:
        listings.append(entry)
    path.write_text(json.dumps(listings, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(f"# wrote {path} (draft={entry['draft']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
