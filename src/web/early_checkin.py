"""
Early check-in form — the owner creates an ad-hoc Igloohome code for a specific
upcoming guest and (optionally) messages it to them immediately.

Flow: pick a property → its upcoming Beds24 reservations appear → pick one →
the validity window defaults to arrival 14:00 → departure 12:00. Two buttons:
"Create only" (just make the code) and "Create & send" (also message the guest
the code, its validity, and that it replaces the automatic one).
"""

import html
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.config.device_map import load_device_map
from src.config.property_map import load_property_map
from src.ports.door_lock import DoorCodeRequest, DoorLockError
from src.ports.reservations import BookingGatewayError, Reservation
from src.web.door_codes import _clamp_start, _round_to_hours
from src.web.layout import brand, code_result, page

log = logging.getLogger(__name__)

router = APIRouter()

# How far ahead to list reservations for the property.
_LOOKAHEAD_DAYS = 60


def _reservations_json(reservations: list[Reservation]) -> str:
    """Group reservations by propertyId for the client-side dropdown filter."""
    grouped: dict[str, list[dict]] = {}
    for r in reservations:
        grouped.setdefault(str(r.property_id), []).append(
            {
                "id": r.booking_id,
                "name": r.guest_name,
                "arrival": r.arrival,
                "departure": r.departure,
                "channel": r.channel,
            }
        )
    return json.dumps(grouped)


# The reservation dropdown is populated client-side from the embedded JSON when a
# property is chosen; selecting a reservation fills the validity window. Kept as a
# plain (non-f) string so the JS braces need no escaping; __RES__ is substituted.
_FORM_JS = """
<script>
  (function () {
    var RES = __RES__;
    var prop = document.getElementById('property');
    var resSel = document.getElementById('reservation');
    var guest = document.getElementById('guest_name');
    var startI = document.getElementById('start_date');
    var endI = document.getElementById('end_date');

    function fmt(d) { if (!d) return ''; var p = d.split('-'); return p[2] + '/' + p[1]; }

    prop.addEventListener('change', function () {
      var opt = prop.options[prop.selectedIndex];
      var pid = opt ? opt.getAttribute('data-pid') : '';
      var list = RES[pid] || [];
      resSel.innerHTML = '';
      startI.value = ''; endI.value = ''; guest.value = '';
      if (!list.length) {
        resSel.appendChild(new Option('No upcoming reservations', ''));
        resSel.disabled = true;
        return;
      }
      resSel.disabled = false;
      resSel.appendChild(new Option('— Select reservation —', ''));
      list.forEach(function (r) {
        var label = fmt(r.arrival) + ' → ' + fmt(r.departure) + ' · ' + r.name +
                    (r.channel ? ' (' + r.channel + ')' : '');
        var o = new Option(label, r.id);
        o.dataset.arrival = r.arrival;
        o.dataset.departure = r.departure;
        o.dataset.name = r.name;
        resSel.appendChild(o);
      });
    });

    resSel.addEventListener('change', function () {
      var o = resSel.options[resSel.selectedIndex];
      if (!o || !o.value) { startI.value = ''; endI.value = ''; guest.value = ''; return; }
      // Dates come from the reservation; the hours keep their defaults (14 / 12),
      // so an early check-in is just a change of the start hour.
      startI.value = o.dataset.arrival;
      endI.value = o.dataset.departure;
      guest.value = o.dataset.name || '';
    });
  })();
</script>
"""


def _hour_options(selected: int) -> str:
    """<option> list for an hour dropdown (00:00–23:00), one pre-selected."""
    return "\n".join(
        f'<option value="{h:02d}"{" selected" if h == selected else ""}>{h:02d}:00</option>'
        for h in range(24)
    )


def _form_page(
    *,
    reservations: list[Reservation],
    error: str = "",
    note: str = "",
) -> str:
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    note_html = f'<p class="hint">{html.escape(note)}</p>' if note else ""

    pm = load_property_map()
    options = ['<option value="">— Select property —</option>']
    for name in pm.property_names:
        options.append(
            f'<option value="{html.escape(name)}" data-pid="{pm.id_for(name)}">'
            f"{html.escape(name)}</option>"
        )
    property_options = "\n".join(options)

    js = _FORM_JS.replace("__RES__", _reservations_json(reservations))

    content = f"""{brand(logo="🏠", heading="Early check-in code",
                         subtitle="Create a code for a guest and send it")}
    {error_html}
    <form method="post" action="/early-checkin">
      <label for="property">Property</label>
      <select id="property" name="property_name" required>
        {property_options}
      </select>
      <label for="reservation">Reservation</label>
      <select id="reservation" name="reservation_id" required disabled>
        <option value="">— Select property first —</option>
      </select>
      {note_html}
      <input type="hidden" id="guest_name" name="guest_name" value="" />
      <label for="start_date">Valid from</label>
      <div class="dt-row">
        <input id="start_date" type="date" name="start_date" required />
        <select id="start_hour" name="start_hour" class="hour" aria-label="Start hour">
          {_hour_options(14)}
        </select>
      </div>
      <label for="end_date">Valid until</label>
      <div class="dt-row">
        <input id="end_date" type="date" name="end_date" required />
        <select id="end_hour" name="end_hour" class="hour" aria-label="End hour">
          {_hour_options(12)}
        </select>
      </div>
      <p class="hint">Date fills in from the reservation — for an early check-in
         just change the start hour. Defaults: from 14:00 → until 12:00.</p>
      <button type="submit" name="action" value="create_send">Create &amp; send to guest</button>
      <button type="submit" name="action" value="create" class="secondary">Create only</button>
    </form>
    {js}
    <p class="links"><a href="/door-codes">Ad-hoc code</a> · <a href="/review">Drafts</a> · <a href="/logout">Logout</a></p>"""
    return page(title="Early check-in", content=content, max_width="440px")


def _fmt_dt(iso: str) -> str:
    """'2026-07-14T14:00:00' → '14/07/2026 14:00' for the guest message."""
    return datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M").strftime("%d/%m/%Y %H:%M")


def _compose_message(code: str, starts_at: str, ends_at: str) -> str:
    """Bilingual FR/EN message: the code, its validity, and that it replaces the
    automatically-sent one. Sent verbatim — Beds24 does not resolve placeholders."""
    s, e = _fmt_dt(starts_at), _fmt_dt(ends_at)
    return (
        "Bonjour,\n\n"
        f"Voici votre code d'accès pour votre arrivée : {code}\n"
        f"Il est valable du {s} au {e}.\n"
        "Merci d'utiliser ce code plutôt que celui qui vous sera (ou vous a été) "
        "envoyé automatiquement pour votre séjour.\n\n"
        "———\n\n"
        "Hello,\n\n"
        f"Here is your access code for your arrival: {code}\n"
        f"It is valid from {s} to {e}.\n"
        "Please use this code instead of the one sent to you automatically for your stay."
    )


async def _render_form(request: Request, *, error: str = "") -> HTMLResponse:
    """Render the form, (re)loading upcoming reservations from the gateway."""
    gateway = getattr(request.app.state, "booking_gateway", None)
    reservations: list[Reservation] = []
    note = ""
    if gateway is None:
        note = "Reservations unavailable — Beds24 is not configured on this server."
    else:
        try:
            reservations = await gateway.upcoming_arrivals(_LOOKAHEAD_DAYS)
        except BookingGatewayError as exc:
            log.error("Loading reservations failed: %s", exc)
            note = f"Could not load reservations: {exc}"
    return HTMLResponse(
        _form_page(reservations=reservations, error=error, note=note),
        status_code=400 if error else 200,
    )


@router.get("/early-checkin", response_class=HTMLResponse)
async def early_checkin_form(request: Request):
    return await _render_form(request)


@router.post("/early-checkin", response_class=HTMLResponse)
async def create_early_checkin(request: Request):
    form = await request.form()
    action = str(form.get("action", "")).strip()
    property_name = str(form.get("property_name", "")).strip()
    reservation_id_raw = str(form.get("reservation_id", "")).strip()
    guest_name = str(form.get("guest_name", "")).strip()
    # The form submits date + hour separately (hour is the field the owner nudges
    # for an early check-in); recombine into the "YYYY-MM-DDTHH:MM" the rounding
    # helpers expect.
    start_date = str(form.get("start_date", "")).strip()
    end_date = str(form.get("end_date", "")).strip()
    start_hour = str(form.get("start_hour", "")).strip()
    end_hour = str(form.get("end_hour", "")).strip()
    starts_at_raw = f"{start_date}T{start_hour}:00" if start_date and start_hour else ""
    ends_at_raw = f"{end_date}T{end_hour}:00" if end_date and end_hour else ""

    door_lock = getattr(request.app.state, "door_lock", None)
    if door_lock is None:
        return await _render_form(
            request, error="Door lock gateway is not configured (set MAKE_IGLOOHOME_WEBHOOK_URL)."
        )
    if not property_name:
        return await _render_form(request, error="Select a property.")
    try:
        booking_id = int(reservation_id_raw)
    except ValueError:
        return await _render_form(request, error="Select a reservation.")

    try:
        starts_at, ends_at = _round_to_hours(starts_at_raw, ends_at_raw)
    except ValueError:
        return await _render_form(request, error="Invalid date/time format.")
    if ends_at <= starts_at:
        return await _render_form(request, error="The end must be after the start.")
    starts_at = _clamp_start(starts_at)
    if ends_at <= starts_at:
        return await _render_form(
            request, error="That window is already over — pick an end time in the future."
        )

    code_request = DoorCodeRequest(
        person_name=guest_name,
        starts_at=starts_at,
        ends_at=ends_at,
        purpose="early_checkin",
        reservation_id=booking_id,
        property_name=property_name,
        device_id=load_device_map().device_for(property_name),
        code_name=guest_name or property_name or "Arrivée anticipée",
    )
    try:
        door_code = await door_lock.create_code(code_request)
    except DoorLockError as exc:
        log.error("Early-checkin code creation failed: %s", exc)
        return await _render_form(request, error=f"Code creation failed: {exc}")

    sent = False
    send_error = ""
    if action == "create_send":
        gateway = getattr(request.app.state, "booking_gateway", None)
        if gateway is None:
            send_error = "Beds24 is not configured — the code was created but not sent."
        else:
            try:
                await gateway.send_guest_message(
                    booking_id, _compose_message(door_code.code, starts_at, ends_at)
                )
                sent = True
            except BookingGatewayError as exc:
                log.error("Sending early-checkin code failed: %s", exc)
                send_error = f"The code was created, but sending failed: {exc}"

    log.info(
        "Early-checkin code created booking=%s window=%s→%s sent=%s",
        booking_id, starts_at, ends_at, sent,
    )
    return HTMLResponse(
        _result_page(
            code=door_code.code,
            guest_name=guest_name,
            property_name=property_name,
            starts_at=starts_at,
            ends_at=ends_at,
            sent=sent,
            send_error=send_error,
        )
    )


def _result_page(
    *,
    code: str,
    guest_name: str,
    property_name: str,
    starts_at: str,
    ends_at: str,
    sent: bool,
    send_error: str,
) -> str:
    if sent:
        status = (
            f'<p class="success" style="text-align:left">Sent to '
            f"<strong>{html.escape(guest_name or 'the guest')}</strong> ✓</p>"
        )
    elif send_error:
        status = f'<p class="error">{html.escape(send_error)}</p>'
    else:
        status = ""

    content = f"""{brand(logo="✅", heading="Code created")}
    {code_result(code)}
    {status}
    <p class="meta">
      {f"For: <strong>{html.escape(guest_name)}</strong><br>" if guest_name else ""}
      {f"Property: {html.escape(property_name)}<br>" if property_name else ""}
      Valid: {starts_at.replace("T", " ")[:16]} &rarr; {ends_at.replace("T", " ")[:16]}
    </p>
    <p class="links"><a href="/early-checkin">Another guest</a> · <a href="/door-codes">Ad-hoc code</a></p>"""
    return page(title="Early check-in — code created", content=content)
