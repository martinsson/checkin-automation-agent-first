"""
Occupancy / free-night view — the owner sees the next N days across every
property with a single job: make empty nights jump out so they can be filled.

Layout A ("free-night grid"): properties down, days across, but colour-inverted —
free nights glow amber, booked nights fade to muted hatching. A free-nights count
runs down the right edge (per property) and along the bottom (per day), so the
emptiest flats and the emptiest dates rank themselves. A single free night wedged
between two booked nights (an "orphan", the hardest to sell) gets a red ring.

Data comes from every configured booking gateway via `stays_overlapping()` — which,
unlike `upcoming_arrivals()`, also returns stays already in progress and owner
blocks, so a night reads as free only when nothing actually covers it.

Below the grid, two companion sections (mock: "occupancy-reservations-section"
artifact, options D + F):
- **Stays timeline** — same day columns, one bar per real stay (guest + channel
  dot). Bookings created/modified in the last 48h get a ring; a cancellation
  stays visible for 48h as a dashed ghost over the nights it released, and those
  nights get a ✦ in the grid.
- **Changes feed** — new / modified / cancelled bookings of the last 7 days via
  `bookings_changed_since()`, the one read that still includes cancellations.
"""

import html
import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.config.property_map import load_property_map
from src.ports.reservations import BookingGatewayError, GuestBookingGateway, Reservation
from src.web.i18n import Translator, lang_from_request
from src.web.layout import brand, page

log = logging.getLogger(__name__)

router = APIRouter()

_DEFAULT_DAYS = 10
_MIN_DAYS = 3
_MAX_DAYS = 21
_FEED_DAYS = 7        # the changes feed looks this many days back
_RECENT_HOURS = 48    # rings / ghosts / ✦ cells highlight changes this fresh
_FEED_MAX_ITEMS = 12

# Weekday / month abbreviations per language (arrays don't fit the flat i18n
# string table, so the page's localisation lives here alongside its plural rules).
_DOW = {
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "fr": ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"],
}
_MON = {
    "en": ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "fr": ["", "janv", "févr", "mars", "avr", "mai", "juin", "juil", "août", "sept", "oct", "nov", "déc"],
}


def _copy(lang: str) -> dict:
    """All occupancy-page copy for `lang`, including the plural forms the grid
    needs. English is the fallback for any unknown language."""
    if lang == "fr":
        return {
            "title": "Nuits libres",
            "heading": "Nuits libres",
            "properties": lambda n: f"{n} logements",
            "summary": lambda g, d: (
                f'<b>{g}</b> nuit{"s" if g != 1 else ""} libre{"s" if g != 1 else ""} '
                f"dans les {d} prochains jours"
            ),
            "orphans": lambda n: f'{n} nuit{"s" if n != 1 else ""} orpheline{"s" if n != 1 else ""}',
            "legend_free": "Libre — à remplir",
            "legend_orphan": "Orpheline (nuit isolée)",
            "legend_booked": "Réservé",
            "range_days": lambda n: f"{n} jours",
            "free_col": "libres",
            "free_per_day": "libres / jour",
            "no_properties": "Aucun logement configuré.",
            "no_backend": "Aucun système de réservation n'est configuré sur ce serveur.",
            "load_failed": lambda src, exc: f"Impossible de charger les réservations {src} : {exc}",
            "legend_fresh": "✦ Libérée récemment (annulation)",
            "stays_title": "Séjours",
            "changes_title": lambda d: f"Changements — {d} derniers jours",
            "badge_new": "Nouveau",
            "badge_cancel": "Annulé",
            "badge_mod": "Modifié",
            "tag_new": "nouv.",
            "tag_mod": "modif.",
            "blocked": "Bloqué",
            "nights": lambda n: f'{n} nuit{"s" if n != 1 else ""}',
            "reopen": lambda n, rng: (
                f'✦ {n} nuit{"s" if n != 1 else ""} remise{"s" if n != 1 else ""} en vente · {rng}'
            ),
            "no_changes": lambda d: f"Aucun changement depuis {d} jours.",
            "when_min": lambda m: f"il y a {m} min",
            "when_h": lambda h: f"il y a {h} h",
            "when_yesterday": "hier",
        }
    return {
        "title": "Free nights",
        "heading": "Free nights",
        "properties": lambda n: f"{n} properties",
        "summary": lambda g, d: (
            f'<b>{g}</b> free night{"s" if g != 1 else ""} in the next {d} days'
        ),
        "orphans": lambda n: f'{n} single-night orphan{"s" if n != 1 else ""}',
        "legend_free": "Free — fill it",
        "legend_orphan": "Orphan (1-night gap)",
        "legend_booked": "Booked",
        "range_days": lambda n: f"{n} days",
        "free_col": "free",
        "free_per_day": "free / day",
        "no_properties": "No properties configured.",
        "no_backend": "No booking backend is configured on this server.",
        "load_failed": lambda src, exc: f"Could not load {src} bookings: {exc}",
        "legend_fresh": "✦ Freed recently (cancellation)",
        "stays_title": "Stays",
        "changes_title": lambda d: f"Changes — last {d} days",
        "badge_new": "New",
        "badge_cancel": "Cancelled",
        "badge_mod": "Modified",
        "tag_new": "new",
        "tag_mod": "mod.",
        "blocked": "Blocked",
        "nights": lambda n: f'{n} night{"s" if n != 1 else ""}',
        "reopen": lambda n, rng: (
            f'✦ {n} night{"s" if n != 1 else ""} back on sale · {rng}'
        ),
        "no_changes": lambda d: f"No changes in the last {d} days.",
        "when_min": lambda m: f"{m} min ago",
        "when_h": lambda h: f"{h} h ago",
        "when_yesterday": "yesterday",
    }

# Grid styling. Scoped `oc-` classes so nothing collides with the shared design
# system in layout.py. Light-only, to match the rest of the owner console.
_STYLE = """
<style>
  .oc-summary { text-align: center; margin: -0.5rem 0 1rem; font-size: 0.95rem; color: #374151; }
  .oc-summary b { color: #b26a00; font-size: 1.15rem; }
  .oc-summary .orphan { color: #b3261e; }
  .oc-legend {
    display: flex; flex-wrap: wrap; justify-content: center; gap: 0.4rem 1rem;
    font-size: 0.78rem; color: #6b7280; margin-bottom: 1rem;
  }
  .oc-legend span { display: inline-flex; align-items: center; gap: 0.35rem; }
  .oc-sw { width: 0.85rem; height: 0.85rem; border-radius: 3px; border: 1px solid #cfd8dc; flex: none; }
  .oc-sw.free { background: #f6a821; border-color: #f6a821; }
  .oc-sw.orphan { background: #f6a821; border: 2px solid #d24b4b; }
  .oc-sw.booked { background: #c4d0d4; border-color: #c4d0d4; }

  .oc-range { text-align: center; font-size: 0.82rem; margin-bottom: 1rem; }
  .oc-range a { color: #2d6a4f; text-decoration: none; font-weight: 600; padding: 0.1rem 0.5rem; }
  .oc-range a.on { background: #eaf3ee; border-radius: 6px; }

  .oc-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  /* Fixed layout + a shared <colgroup> keep the grid and the stays timeline on
     the exact same day columns (both tables live in the same scroll container). */
  table.oc-grid { border-collapse: collapse; width: 100%; font-size: 0.8rem; table-layout: fixed; }
  table.oc-grid col.c-prop { width: 7.5rem; }
  table.oc-grid col.c-tot { width: 2.9rem; }
  table.oc-grid th, table.oc-grid td { text-align: center; padding: 0; }
  table.oc-grid thead th {
    font-weight: 600; color: #6b7280; padding: 0.25rem 0.15rem 0.5rem;
    font-variant-numeric: tabular-nums; white-space: nowrap;
  }
  table.oc-grid thead th .dow {
    display: block; font-size: 0.62rem; text-transform: uppercase;
    letter-spacing: 0.04em; color: #9aa5ac;
  }
  table.oc-grid th.wknd { color: #1a1a1a; }
  table.oc-grid th.today .dow { color: #2d6a4f; font-weight: 700; }
  table.oc-grid th.prop {
    text-align: left; font-weight: 600; color: #1a1a1a; white-space: nowrap;
    padding: 0 0.7rem 0 0.15rem; position: sticky; left: 0; background: #fff; z-index: 1;
  }
  table.oc-grid th.tot-h { color: #6b7280; padding-left: 0.4rem; }
  table.oc-grid td.cell { border: 1px solid #e3e9ec; height: 2.35rem; position: relative; }
  table.oc-grid td.booked { background: #eef2f3; }
  table.oc-grid td.booked::after {
    content: ""; position: absolute; inset: 0;
    background: repeating-linear-gradient(-45deg, transparent 0 5px, rgba(150,166,172,0.55) 5px 6px);
  }
  table.oc-grid td.free { background: #fff2d6; }
  table.oc-grid td.free .dot {
    position: absolute; inset: 0; margin: auto; width: 0.5rem; height: 0.5rem;
    border-radius: 50%; background: #f6a821;
  }
  table.oc-grid td.orphan { box-shadow: inset 0 0 0 2px #d24b4b; }
  table.oc-grid td.orphan .dot { background: #d24b4b; }
  table.oc-grid td.today-col { outline: 2px solid rgba(45,106,79,0.35); outline-offset: -2px; }
  table.oc-grid td.tot {
    font-weight: 700; color: #8a5a00; font-variant-numeric: tabular-nums; padding-left: 0.4rem;
  }
  table.oc-grid td.tot.zero { color: #9aa5ac; font-weight: 500; }
  table.oc-grid tfoot td {
    border-top: 2px solid #cfd8dc; font-weight: 700; color: #8a5a00;
    font-variant-numeric: tabular-nums; padding-top: 0.35rem;
  }
  table.oc-grid tfoot td.lbl { text-align: left; color: #6b7280; font-weight: 600; white-space: nowrap; }
  table.oc-grid tfoot td.z { color: #9aa5ac; font-weight: 500; }

  .oc-empty { text-align: center; color: #6b7280; margin: 1.5rem 0; }

  /* freshly-freed night (cancellation < 48h) */
  table.oc-grid td.fresh { box-shadow: inset 0 0 0 2px rgba(246,168,33,0.8); }
  table.oc-grid td.fresh::before {
    content: "✦"; position: absolute; top: -0.05rem; right: 0.1rem;
    font-size: 0.6rem; color: #8a5a00; z-index: 2;
  }
  table.oc-grid td.orphan.fresh { box-shadow: inset 0 0 0 2px #d24b4b; }

  .oc-sect {
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.09em;
    font-weight: 700; color: #9aa5ac; margin: 1.6rem 0 0.7rem;
    position: sticky; left: 0; /* stays put when the scroll container pans */
  }

  /* channel dot */
  .oc-ch { width: 0.55rem; height: 0.55rem; border-radius: 50%; display: inline-block; flex: none; background: #9aa5ac; }
  .oc-ch.airbnb { background: #ff5a5f; }
  .oc-ch.booking { background: #4a7fc1; }
  .oc-ch.direct { background: #2d6a4f; }

  /* stays timeline (a second table on the grid's colgroup → same day columns) */
  table.oc-tl th.prop { font-size: 0.82rem; }
  table.oc-tl td.tl-td { padding: 0; }
  table.oc-tl tr + tr th, table.oc-tl tr + tr td { border-top: 1px solid #e3e9ec; }
  .oc-tl-track { position: relative; height: 2.3rem; background: #fff2d6; }
  .oc-tl-track .lines { position: absolute; inset: 0; display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; }
  .oc-tl-track .lines i { border-left: 1px solid rgba(246,168,33,0.25); }
  .oc-tl-track .lines i.today { box-shadow: inset 1px 0 0 #2d6a4f; }
  .oc-tl-bar {
    position: absolute; top: 0.32rem; height: 1.66rem; border-radius: 6px;
    background: #c4d0d4; color: #55656c; font-size: 0.7rem; font-weight: 600;
    display: flex; align-items: center; gap: 0.35rem; padding: 0 0.5rem;
    overflow: hidden; white-space: nowrap;
  }
  .oc-tl-bar .n { overflow: hidden; text-overflow: ellipsis; }
  .oc-tl-bar .tag { font-size: 0.58rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.05em; }
  .oc-tl-bar.is-new { box-shadow: 0 0 0 2px #2d6a4f; color: #1a1a1a; }
  .oc-tl-bar.is-new .tag { color: #2d6a4f; }
  .oc-tl-bar.is-mod { box-shadow: 0 0 0 2px #1e3a5f; }
  .oc-tl-bar.is-mod .tag { color: #1e3a5f; }
  .oc-tl-ghost {
    position: absolute; top: 0.32rem; height: 1.66rem; border-radius: 6px;
    border: 2px dashed #d24b4b; color: #d24b4b; font-size: 0.66rem; font-weight: 700;
    display: flex; align-items: center; padding: 0 0.45rem; white-space: nowrap;
    overflow: hidden; background: transparent;
  }

  /* changes feed */
  .oc-feed { }
  .oc-ev { display: flex; gap: 0.7rem; padding: 0.65rem 0.2rem; align-items: flex-start; }
  .oc-ev + .oc-ev { border-top: 1px solid #e3e9ec; }
  .oc-badge {
    flex: none; font-size: 0.62rem; font-weight: 800; text-transform: uppercase;
    letter-spacing: 0.06em; border-radius: 6px; padding: 0.18rem 0.5rem;
    margin-top: 0.15rem; min-width: 4.6rem; text-align: center;
  }
  .oc-badge.new { background: #eaf3ee; color: #2d6a4f; }
  .oc-badge.cancel { background: #fdecea; color: #b3261e; }
  .oc-badge.mod { background: #e8eef6; color: #1e3a5f; }
  .oc-ev-body { flex: 1; min-width: 0; }
  .oc-ev-l1 { font-size: 0.88rem; display: flex; align-items: center; gap: 0.45rem; flex-wrap: wrap; }
  .oc-ev-l1 .who { font-weight: 650; }
  .oc-ev-l1 .where { color: #5a6b73; }
  .oc-ev-l2 { font-size: 0.8rem; color: #5a6b73; font-variant-numeric: tabular-nums; margin-top: 0.1rem; }
  .oc-ev-l2 s { color: #9aa5ac; }
  .oc-reopen {
    display: inline-block; margin-top: 0.3rem; font-size: 0.76rem; font-weight: 700;
    color: #8a5a00; background: #fff2d6; border: 1px solid #f6a821;
    border-radius: 8px; padding: 0.12rem 0.55rem;
  }
  .oc-ev-when { flex: none; font-size: 0.72rem; color: #9aa5ac; margin-top: 0.25rem; white-space: nowrap; font-variant-numeric: tabular-nums; }
  .oc-nochanges { font-size: 0.82rem; color: #9aa5ac; font-style: italic; margin: 0.2rem 0 0; }
</style>
"""


def _gateways(request: Request) -> dict[str, GuestBookingGateway]:
    """source → configured gateway (Beds24 for the six flats, Smoobu for Hippocrate)."""
    out: dict[str, GuestBookingGateway] = {}
    beds24 = getattr(request.app.state, "booking_gateway", None)
    if beds24 is not None:
        out["beds24"] = beds24
    smoobu = getattr(request.app.state, "smoobu_gateway", None)
    if smoobu is not None:
        out["smoobu"] = smoobu
    return out


def _property_list(gateways: dict[str, GuestBookingGateway]) -> list[tuple[str, int]]:
    """(name, property_id) for every unit, in a stable order: the Beds24 flats from
    the YAML map, then any non-Beds24 units a gateway contributes (e.g. Hippocrate)."""
    pm = load_property_map()
    props: list[tuple[str, int]] = [
        (n, pid) for n in pm.property_names if (pid := pm.id_for(n)) is not None
    ]
    seen = {pid for _, pid in props}
    for gw in gateways.values():
        for name, pid in gw.managed_properties():
            if pid not in seen:
                props.append((name, int(pid)))
                seen.add(pid)
    return props


def _occupied(reservations: list[Reservation], day_iso: str) -> bool:
    """True if any stay covers the night of `day_iso` (arrival ≤ day < departure)."""
    return any(r.arrival and r.departure and r.arrival <= day_iso < r.departure for r in reservations)


def _build_grid(props, days, by_prop):
    """Return (rows, per_day_free, grand_total). Each row: (name, [(free, orphan)], free_count)."""
    day_isos = [d.isoformat() for d in days]
    n = len(days)
    rows = []
    per_day_free = [0] * n
    grand = 0
    for name, pid in props:
        res = by_prop.get(pid, [])
        occ = [_occupied(res, iso) for iso in day_isos]
        cells = []
        free_count = 0
        for i in range(n):
            free = not occ[i]
            # Orphan: a lone free night flanked by booked nights on both sides.
            orphan = free and 0 < i < n - 1 and occ[i - 1] and occ[i + 1]
            if free:
                free_count += 1
                per_day_free[i] += 1
                grand += 1
            cells.append((free, orphan))
        rows.append((name, cells, free_count))
    return rows, per_day_free, grand


def _parse_dt(s: str) -> datetime | None:
    """Lenient provider-timestamp parser: ISO with T or space, optional Z/offset.
    Returns a naive local datetime, or None when absent/unparseable."""
    s = (s or "").strip()
    if not s:
        return None
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _parse_date(s: str) -> date | None:
    try:
        return date.fromisoformat((s or "").strip())
    except ValueError:
        return None


def _channel_class(channel: str) -> str:
    """CSS class for the channel dot: airbnb / booking / direct / (grey) other."""
    c = (channel or "").lower()
    if "airbnb" in c:
        return "airbnb"
    if "booking" in c:
        return "booking"
    if not c or "direct" in c or "beds24" in c or "smoobu" in c or "homepage" in c:
        return "direct"
    return "other"


_CANCELLED_STATUSES = {"cancelled"}
_BLOCK_STATUSES = {"black", "block"}


def _change_kind(r: Reservation, since: datetime) -> str:
    """cancel / new / mod — cancelled wins; else "new" when the booking itself
    was created inside the feed window, otherwise it's a modification."""
    if r.status in _CANCELLED_STATUSES:
        return "cancel"
    bt = _parse_dt(r.booking_time)
    if bt is not None and bt >= since:
        return "new"
    return "mod"


def _nights(r: Reservation) -> int:
    a, d = _parse_date(r.arrival), _parse_date(r.departure)
    return max(0, (d - a).days) if a and d else 0


def _range_label(r: Reservation, mon: list[str]) -> str:
    a, d = _parse_date(r.arrival), _parse_date(r.departure)
    if not a or not d:
        return f"{r.arrival}–{r.departure}"
    if a.month == d.month:
        return f"{a.day}–{d.day} {mon[a.month]}"
    return f"{a.day} {mon[a.month]} – {d.day} {mon[d.month]}"


def _when_label(dt: datetime | None, now: datetime, c: dict, mon: list[str]) -> str:
    if dt is None:
        return ""
    delta = now - dt
    if delta < timedelta(hours=1):
        return c["when_min"](max(1, int(delta.total_seconds() // 60)))
    if delta < timedelta(hours=24) and dt.date() == now.date():
        return c["when_h"](int(delta.total_seconds() // 3600))
    if dt.date() == now.date() - timedelta(days=1):
        return c["when_yesterday"]
    return f"{dt.day} {mon[dt.month]}"


def _recent_cancellations(changes: list[Reservation], now: datetime) -> list[Reservation]:
    """Cancellations fresh enough (< _RECENT_HOURS) to ghost in the timeline and
    ✦-mark the nights they released."""
    cutoff = now - timedelta(hours=_RECENT_HOURS)
    out = []
    for r in changes:
        if r.status not in _CANCELLED_STATUSES:
            continue
        ts = _parse_dt(r.modified_time) or _parse_dt(r.booking_time)
        if ts is not None and ts >= cutoff:
            out.append(r)
    return out


def _fresh_cells(cancels: list[Reservation], days: list[date]) -> set[tuple[int, str]]:
    """(property_id, night-iso) pairs inside the window released by a recent
    cancellation. The caller only marks them when the night still reads as free
    — a re-booked night stays a plain booked cell."""
    out: set[tuple[int, str]] = set()
    for r in cancels:
        a, d = _parse_date(r.arrival), _parse_date(r.departure)
        if not a or not d:
            continue
        for day in days:
            if a <= day < d:
                out.add((r.property_id, day.isoformat()))
    return out


def _bar_span(r: Reservation, days: list[date]) -> tuple[float, float] | None:
    """(left%, width%) of a stay across the day window, clamped; None if outside."""
    n = len(days)
    a, d = _parse_date(r.arrival), _parse_date(r.departure)
    if not a or not d or n == 0:
        return None
    s_idx = max(0, (a - days[0]).days)
    e_idx = min(n, (d - days[0]).days)
    if e_idx <= s_idx or s_idx >= n:
        return None
    return (s_idx / n * 100, (e_idx - s_idx) / n * 100)


def _colgroup(n_days: int) -> str:
    """Shared column skeleton: property | n day columns | totals. Both the grid
    and the stays timeline use it (with table-layout: fixed) so their day
    columns align to the pixel."""
    return '<colgroup><col class="c-prop">' + "<col>" * n_days + '<col class="c-tot"></colgroup>'


def _table_style(n_days: int) -> str:
    """min-width so fixed-layout day columns never squeeze below ~2.2rem each."""
    return f"min-width:{10.4 + n_days * 2.2:.1f}rem"


def _render_timeline(props, days, by_prop, ghosts, now: datetime, c: dict) -> str:
    """Option D: one track per property, same day columns as the grid. Stays are
    grey bars (guest + channel dot); < 48h-old creations/modifications get a
    ring, fresh cancellations a dashed ghost over the nights they released."""
    today = date.today()
    cutoff = now - timedelta(hours=_RECENT_HOURS)
    lines = "".join(
        f'<i class="{"today" if d == today else ""}"></i>' for d in days
    )
    rows = ""
    for name, pid in props:
        bars = ""
        for g in ghosts:
            if g.property_id != pid:
                continue
            span = _bar_span(g, days)
            if span is None:
                continue
            gname = html.escape(g.guest_name or c["blocked"])
            bars += (
                f'<div class="oc-tl-ghost" style="left:{span[0]:.3f}%;'
                f'width:calc({span[1]:.3f}% - 2px)">{gname} ✕</div>'
            )
        for r in sorted(by_prop.get(pid, []), key=lambda r: r.arrival):
            span = _bar_span(r, days)
            if span is None:
                continue
            is_block = r.status in _BLOCK_STATUSES
            bt, mt = _parse_dt(r.booking_time), _parse_dt(r.modified_time)
            is_new = not is_block and bt is not None and bt >= cutoff
            is_mod = not is_block and not is_new and mt is not None and mt >= cutoff
            cls = "oc-tl-bar" + (" is-new" if is_new else "") + (" is-mod" if is_mod else "")
            label = html.escape(r.guest_name) if not is_block else html.escape(c["blocked"])
            dot = "" if is_block else f'<span class="oc-ch {_channel_class(r.channel)}"></span>'
            tag = ""
            if is_new:
                tag = f'<span class="tag">{html.escape(c["tag_new"])}</span>'
            elif is_mod:
                tag = f'<span class="tag">{html.escape(c["tag_mod"])}</span>'
            bars += (
                f'<div class="{cls}" style="left:{span[0]:.3f}%;'
                f'width:calc({span[1]:.3f}% - 2px)">{dot}<span class="n">{label}</span>{tag}</div>'
            )
        rows += (
            f'<tr><th class="prop">{html.escape(name)}</th>'
            f'<td class="tl-td" colspan="{len(days)}"><div class="oc-tl-track">'
            f'<div class="lines">{lines}</div>{bars}</div></td><td></td></tr>'
        )
    return (
        f'<p class="oc-sect">{html.escape(c["stays_title"])}</p>'
        f'<table class="oc-grid oc-tl" style="{_table_style(len(days))}">'
        f"{_colgroup(len(days))}<tbody>{rows}</tbody></table>"
    )


def _render_feed(changes, prop_names: dict[int, str], since: datetime, now: datetime,
                 c: dict, mon: list[str]) -> str:
    """Option F: new / modified / cancelled bookings of the feed window, newest
    first. A cancellation carries the amber "nights back on sale" chip that
    matches the ✦ cells in the grid."""
    today = date.today()
    items = []
    for r in changes:
        if r.status in _BLOCK_STATUSES:
            continue  # owner blocks aren't guest-booking news
        dep = _parse_date(r.departure)
        if dep is None or dep < today:
            continue  # changes to already-departed stays aren't actionable
        ts = _parse_dt(r.modified_time) or _parse_dt(r.booking_time)
        items.append((ts or datetime.min, r))
    items.sort(key=lambda t: t[0], reverse=True)

    rows = ""
    for ts, r in items[:_FEED_MAX_ITEMS]:
        kind = _change_kind(r, since)
        badge_cls = {"new": "new", "cancel": "cancel", "mod": "mod"}[kind]
        badge_lbl = {"new": c["badge_new"], "cancel": c["badge_cancel"], "mod": c["badge_mod"]}[kind]
        rng, nn = _range_label(r, mon), _nights(r)
        line = f"{rng} · {c['nights'](nn)}"
        if r.price > 0:
            line += f" · {r.price:.0f} €"
        chip = ""
        if kind == "cancel":
            line = f"<s>{line}</s>"
            a = _parse_date(r.arrival)
            resale = _nights(r)
            if a is not None and a < today:  # only the nights still ahead reopen
                dep = _parse_date(r.departure)
                resale = max(0, (dep - today).days) if dep else 0
            if resale > 0:
                chip = f'<span class="oc-reopen">{html.escape(c["reopen"](resale, rng))}</span>'
        where = html.escape(prop_names.get(r.property_id, str(r.property_id)))
        who = html.escape(r.guest_name or c["blocked"])
        when = html.escape(_when_label(None if ts == datetime.min else ts, now, c, mon))
        rows += (
            f'<div class="oc-ev"><span class="oc-badge {badge_cls}">{html.escape(badge_lbl)}</span>'
            f'<div class="oc-ev-body"><div class="oc-ev-l1">'
            f'<span class="oc-ch {_channel_class(r.channel)}"></span>'
            f'<span class="who">{who}</span><span class="where">{where}</span></div>'
            f'<div class="oc-ev-l2">{line}</div>{chip}</div>'
            f'<span class="oc-ev-when">{when}</span></div>'
        )
    if not rows:
        rows = f'<p class="oc-nochanges">{html.escape(c["no_changes"](_FEED_DAYS))}</p>'
    return (
        f'<p class="oc-sect">{html.escape(c["changes_title"](_FEED_DAYS))}</p>'
        f'<div class="oc-feed">{rows}</div>'
    )


def _render(props, days, by_prop, window_days: int, lang: str, note: str = "",
            changes: list[Reservation] | None = None) -> str:
    today = date.today()
    now = datetime.now()
    c = _copy(lang)
    t = Translator(lang)  # shared nav strings
    dow, mon = _DOW.get(lang, _DOW["en"]), _MON.get(lang, _MON["en"])
    rows, per_day_free, grand = _build_grid(props, days, by_prop)
    orphan_total = sum(1 for _, cells, _ in rows for _free, orphan in cells if orphan)

    changes = changes or []
    ghosts = _recent_cancellations(changes, now)
    fresh = _fresh_cells(ghosts, days)

    note_html = f'<p class="hint" style="text-align:center">{html.escape(note)}</p>' if note else ""

    if not props:
        body = f'<p class="oc-empty">{html.escape(c["no_properties"])}</p>'
    else:
        # Header row
        head = '<th class="prop"></th>'
        for d in days:
            cls = []
            if d.weekday() >= 5:
                cls.append("wknd")
            if d == today:
                cls.append("today")
            head += (
                f'<th class="{" ".join(cls)}"><span class="dow">{dow[d.weekday()]}</span>{d.day}</th>'
            )
        head += f'<th class="tot-h">{c["free_col"]}</th>'

        # Property rows (rows come out of _build_grid in props order, so zip is safe)
        body_rows = ""
        for (name, cells, free_count), (_pname, pid) in zip(rows, props):
            tds = f'<th class="prop">{html.escape(name)}</th>'
            for i, (free, orphan) in enumerate(cells):
                cls = ["cell", "free" if free else "booked"]
                if orphan:
                    cls.append("orphan")
                if free and (pid, days[i].isoformat()) in fresh:
                    cls.append("fresh")
                if days[i] == today:
                    cls.append("today-col")
                dot = '<span class="dot"></span>' if free else ""
                tds += f'<td class="{" ".join(cls)}">{dot}</td>'
            tot_cls = "tot zero" if free_count == 0 else "tot"
            tds += f'<td class="{tot_cls}">{free_count}</td>'
            body_rows += f"<tr>{tds}</tr>"

        # Footer: free nights per day
        foot = f'<td class="lbl">{c["free_per_day"]}</td>'
        for cnt in per_day_free:
            foot += f'<td class="{"z" if cnt == 0 else ""}">{cnt}</td>'
        foot += f"<td>{grand}</td>"

        since = now - timedelta(days=_FEED_DAYS)
        prop_names = {pid: name for name, pid in props}
        body = f"""<div class="oc-scroll">
        <table class="oc-grid" style="{_table_style(len(days))}">{_colgroup(len(days))}
          <thead><tr>{head}</tr></thead>
          <tbody>{body_rows}</tbody>
          <tfoot><tr>{foot}</tr></tfoot>
        </table>
        {_render_timeline(props, days, by_prop, ghosts, now, c)}
        </div>
        {_render_feed(changes, prop_names, since, now, c, mon)}"""

    # Range switcher
    def _range_link(nd: int) -> str:
        on = " on" if nd == window_days else ""
        return f'<a class="range{on}" href="/occupancy?days={nd}">{c["range_days"](nd)}</a>'

    range_html = " ".join(_range_link(nd) for nd in (7, 10, 14))

    last = days[-1] if days else today
    subtitle = f"{today.day} {mon[today.month]} → {last.day} {mon[last.month]} · {c['properties'](len(props))}"
    orphan_note = (
        f' · <span class="orphan">{html.escape(c["orphans"](orphan_total))}</span>'
        if orphan_total
        else ""
    )

    content = f"""{_STYLE}
    {brand(logo="🗓️", heading=c["heading"], subtitle=subtitle)}
    <p class="oc-summary">{c["summary"](grand, window_days)}{orphan_note}</p>
    <div class="oc-legend">
      <span><i class="oc-sw free"></i> {html.escape(c["legend_free"])}</span>
      <span><i class="oc-sw orphan"></i> {html.escape(c["legend_orphan"])}</span>
      <span><i class="oc-sw booked"></i> {html.escape(c["legend_booked"])}</span>
      {f'<span>{html.escape(c["legend_fresh"])}</span>' if fresh else ''}
    </div>
    <div class="oc-range">{range_html}</div>
    {note_html}
    {body}
    <p class="links"><a href="/early-checkin">{t("nav.early_checkin")}</a> · <a href="/door-codes">{t("nav.adhoc_code")}</a> · <a href="/review">{t("nav.drafts")}</a> · <a href="/logout">{t("nav.logout")}</a></p>"""
    return page(title=c["title"], content=content, max_width="820px", lang=lang)


@router.get("/occupancy", response_class=HTMLResponse)
async def occupancy(request: Request):
    days_param = request.query_params.get("days", "")
    try:
        window_days = int(days_param)
    except (TypeError, ValueError):
        window_days = _DEFAULT_DAYS
    window_days = max(_MIN_DAYS, min(_MAX_DAYS, window_days))

    lang = lang_from_request(request)
    c = _copy(lang)

    start = date.today()
    end = start + timedelta(days=window_days)
    days = [start + timedelta(days=i) for i in range(window_days)]

    gateways = _gateways(request)
    props = _property_list(gateways)

    by_prop: dict[int, list[Reservation]] = {}
    changes: list[Reservation] = []
    notes: list[str] = []
    if not gateways:
        notes.append(c["no_backend"])
    since = datetime.now() - timedelta(days=_FEED_DAYS)
    for source, gw in gateways.items():
        try:
            for r in await gw.stays_overlapping(start, end):
                by_prop.setdefault(r.property_id, []).append(r)
        except BookingGatewayError as exc:
            log.error("Loading %s occupancy failed: %s", source, exc)
            notes.append(c["load_failed"](source, exc))
        except NotImplementedError:
            log.warning("%s gateway does not support stays_overlapping", source)
        try:
            changes.extend(await gw.bookings_changed_since(since))
        except BookingGatewayError as exc:
            log.error("Loading %s changes failed: %s", source, exc)
            notes.append(c["load_failed"](source, exc))
        except NotImplementedError:
            log.warning("%s gateway does not support bookings_changed_since", source)

    return HTMLResponse(
        _render(props, days, by_prop, window_days, lang, note=" ".join(notes), changes=changes)
    )
