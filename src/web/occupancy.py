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
"""

import html
import logging
from datetime import date, timedelta

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
  table.oc-grid { border-collapse: collapse; width: 100%; font-size: 0.8rem; }
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
  table.oc-grid td.cell { border: 1px solid #e3e9ec; height: 2.35rem; position: relative; min-width: 2rem; }
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


def _render(props, days, by_prop, window_days: int, lang: str, note: str = "") -> str:
    today = date.today()
    c = _copy(lang)
    t = Translator(lang)  # shared nav strings
    dow, mon = _DOW.get(lang, _DOW["en"]), _MON.get(lang, _MON["en"])
    rows, per_day_free, grand = _build_grid(props, days, by_prop)
    orphan_total = sum(1 for _, cells, _ in rows for _free, orphan in cells if orphan)

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

        # Property rows
        body_rows = ""
        for name, cells, free_count in rows:
            tds = f'<th class="prop">{html.escape(name)}</th>'
            for i, (free, orphan) in enumerate(cells):
                cls = ["cell", "free" if free else "booked"]
                if orphan:
                    cls.append("orphan")
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

        body = f"""<div class="oc-scroll"><table class="oc-grid">
          <thead><tr>{head}</tr></thead>
          <tbody>{body_rows}</tbody>
          <tfoot><tr>{foot}</tr></tfoot>
        </table></div>"""

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
    notes: list[str] = []
    if not gateways:
        notes.append(c["no_backend"])
    for source, gw in gateways.items():
        try:
            for r in await gw.stays_overlapping(start, end):
                by_prop.setdefault(r.property_id, []).append(r)
        except BookingGatewayError as exc:
            log.error("Loading %s occupancy failed: %s", source, exc)
            notes.append(c["load_failed"](source, exc))
        except NotImplementedError:
            log.warning("%s gateway does not support stays_overlapping", source)

    return HTMLResponse(_render(props, days, by_prop, window_days, lang, note=" ".join(notes)))
