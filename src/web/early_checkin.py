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
from src.ports.reservations import (
    SOURCE_BEDS24,
    SOURCE_SMOOBU,
    BookingGatewayError,
    GuestBookingGateway,
    Reservation,
)
from src.web.door_codes import _clamp_start, _round_to_hours
from src.web.i18n import Translator, translator_for
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
                "lang": r.language,
                "source": r.source,
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
    var langI = document.getElementById('guest_language');
    var srcI = document.getElementById('source');

    function fmt(d) { if (!d) return ''; var p = d.split('-'); return p[2] + '/' + p[1]; }

    prop.addEventListener('change', function () {
      var opt = prop.options[prop.selectedIndex];
      var pid = opt ? opt.getAttribute('data-pid') : '';
      var list = RES[pid] || [];
      resSel.innerHTML = '';
      startI.value = ''; endI.value = ''; guest.value = ''; srcI.value = '';
      if (!list.length) {
        resSel.appendChild(new Option(__NO_RES__, ''));
        resSel.disabled = true;
        return;
      }
      resSel.disabled = false;
      resSel.appendChild(new Option(__SELECT_RES__, ''));
      list.forEach(function (r) {
        var label = fmt(r.arrival) + ' → ' + fmt(r.departure) + ' · ' + r.name +
                    (r.channel ? ' (' + r.channel + ')' : '');
        var o = new Option(label, r.id);
        o.dataset.arrival = r.arrival;
        o.dataset.departure = r.departure;
        o.dataset.name = r.name;
        o.dataset.lang = r.lang || '';
        o.dataset.source = r.source || '';
        resSel.appendChild(o);
      });
    });

    resSel.addEventListener('change', function () {
      var o = resSel.options[resSel.selectedIndex];
      if (!o || !o.value) { startI.value = ''; endI.value = ''; guest.value = ''; langI.value = ''; srcI.value = ''; return; }
      // Dates come from the reservation; the hours keep their defaults (14 / 12),
      // so an early check-in is just a change of the start hour.
      startI.value = o.dataset.arrival;
      endI.value = o.dataset.departure;
      guest.value = o.dataset.name || '';
      langI.value = o.dataset.lang || '';
      srcI.value = o.dataset.source || '';
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
    t: Translator,
    *,
    reservations: list[Reservation],
    error: str = "",
    note: str = "",
    extra_properties: list[tuple[str, int]] | None = None,
) -> str:
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    note_html = f'<p class="hint">{html.escape(note)}</p>' if note else ""

    pm = load_property_map()
    # Beds24 properties (from the YAML map) plus any non-Beds24 units contributed
    # by another gateway (e.g. Hippocrate from Smoobu). data-pid MUST equal the
    # property_id on that unit's reservations so the client-side filter matches.
    props: list[tuple[str, int | None]] = [(n, pm.id_for(n)) for n in pm.property_names]
    props.extend(extra_properties or [])
    options = [f'<option value="">{html.escape(t("early.select_property"))}</option>']
    for name, pid in props:
        options.append(
            f'<option value="{html.escape(name)}" data-pid="{pid}">'
            f"{html.escape(name)}</option>"
        )
    property_options = "\n".join(options)

    js = (
        _FORM_JS.replace("__RES__", _reservations_json(reservations))
        .replace("__NO_RES__", json.dumps(t("early.no_reservations"), ensure_ascii=False))
        .replace("__SELECT_RES__", json.dumps(t("early.select_reservation"), ensure_ascii=False))
    )

    content = f"""{brand(logo="🏠", heading=t("early.heading"),
                         subtitle=t("early.subtitle"))}
    {error_html}
    <form method="post" action="/early-checkin">
      <label for="property">{t("common.property")}</label>
      <select id="property" name="property_name" required>
        {property_options}
      </select>
      <label for="reservation">{t("early.reservation")}</label>
      <select id="reservation" name="reservation_id" required disabled>
        <option value="">{html.escape(t("early.select_property_first"))}</option>
      </select>
      {note_html}
      <input type="hidden" id="guest_name" name="guest_name" value="" />
      <input type="hidden" id="guest_language" name="guest_language" value="" />
      <input type="hidden" id="source" name="source" value="" />
      <label for="start_date">{t("early.valid_from")}</label>
      <div class="dt-row">
        <input id="start_date" type="date" name="start_date" required />
        <select id="start_hour" name="start_hour" class="hour" aria-label="{html.escape(t("early.start_hour"))}">
          {_hour_options(14)}
        </select>
      </div>
      <label for="end_date">{t("early.valid_until")}</label>
      <div class="dt-row">
        <input id="end_date" type="date" name="end_date" required />
        <select id="end_hour" name="end_hour" class="hour" aria-label="{html.escape(t("early.end_hour"))}">
          {_hour_options(12)}
        </select>
      </div>
      <p class="hint">{t("early.hint")}</p>
      <button type="submit">{t("common.create_code")}</button>
    </form>
    {js}
    <p class="links"><a href="/occupancy">{t("nav.free_nights")}</a> · <a href="/door-codes">{t("nav.adhoc_code")}</a> · <a href="/review">{t("nav.drafts")}</a> · <a href="/logout">{t("nav.logout")}</a></p>"""
    return page(title=t("early.title"), content=content, max_width="440px", lang=t.lang)


def _fmt_dt(iso: str) -> str:
    """'2026-07-14T14:00:00' → '14/07/2026 14:00' for the guest message."""
    return datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M").strftime("%d/%m/%Y %H:%M")


def _compose_message(code: str, starts_at: str, ends_at: str, language: str = "") -> str:
    """The pre-filled message: the code, its validity, and that it replaces the
    automatically-sent one. French when the guest's language is French, English
    otherwise. Sent verbatim — Beds24 does not resolve placeholders."""
    s, e = _fmt_dt(starts_at), _fmt_dt(ends_at)
    if language.strip().lower().startswith("fr"):
        return (
            "Bonjour,\n\n"
            f"Voici votre code d'accès pour votre arrivée : {code}\n"
            f"Il est valable du {s} au {e}.\n"
            "Merci d'utiliser ce code plutôt que celui qui vous sera (ou vous a été) "
            "envoyé automatiquement pour votre séjour.\n\n"
            "Bonne arrivée !"
        )
    return (
        "Hello,\n\n"
        f"Here is your access code for your arrival: {code}\n"
        f"It is valid from {s} to {e}.\n"
        "Please use this code instead of the one sent to you automatically for your stay.\n\n"
        "Safe travels!"
    )


def _lang_label(t: Translator, language: str) -> str:
    """Name of the *guest message* language (French/English), rendered in the UI
    language — this labels which language the pre-filled message is written in."""
    key = "lang.french" if language.strip().lower().startswith("fr") else "lang.english"
    return t(key)


def _gateways(request: Request) -> dict[str, GuestBookingGateway]:
    """source → configured booking gateway. Beds24 powers the six Beds24 flats;
    Smoobu (if configured) adds L'Hippocrate."""
    out: dict[str, GuestBookingGateway] = {}
    beds24 = getattr(request.app.state, "booking_gateway", None)
    if beds24 is not None:
        out[SOURCE_BEDS24] = beds24
    smoobu = getattr(request.app.state, "smoobu_gateway", None)
    if smoobu is not None:
        out[SOURCE_SMOOBU] = smoobu
    return out


async def _render_form(request: Request, *, error: str = "") -> HTMLResponse:
    """Render the form, (re)loading upcoming reservations from every gateway."""
    t = translator_for(request)
    gateways = _gateways(request)
    reservations: list[Reservation] = []
    extra_properties: list[tuple[str, int]] = []
    notes: list[str] = []
    if not gateways:
        notes.append(t("early.no_backend"))
    for source, gateway in gateways.items():
        try:
            reservations.extend(await gateway.upcoming_arrivals(_LOOKAHEAD_DAYS))
        except BookingGatewayError as exc:
            log.error("Loading %s reservations failed: %s", source, exc)
            notes.append(t("early.load_failed", source=source, exc=exc))
        # Non-Beds24 units aren't in the YAML property map — add them to the dropdown.
        extra_properties.extend(gateway.managed_properties())
    return HTMLResponse(
        _form_page(
            t,
            reservations=reservations,
            error=error,
            note=" ".join(notes),
            extra_properties=extra_properties,
        ),
        status_code=400 if error else 200,
    )


@router.get("/early-checkin", response_class=HTMLResponse)
async def early_checkin_form(request: Request):
    return await _render_form(request)


@router.post("/early-checkin", response_class=HTMLResponse)
async def create_early_checkin(request: Request):
    """Create the code, then show it with a pre-filled, editable message and a
    Send button (the send itself is POST /early-checkin/send)."""
    t = translator_for(request)
    form = await request.form()
    property_name = str(form.get("property_name", "")).strip()
    reservation_id_raw = str(form.get("reservation_id", "")).strip()
    guest_name = str(form.get("guest_name", "")).strip()
    guest_language = str(form.get("guest_language", "")).strip()
    source = str(form.get("source", "")).strip() or SOURCE_BEDS24
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
        return await _render_form(request, error=t("err.no_gateway"))
    if not property_name:
        return await _render_form(request, error=t("err.select_property"))
    try:
        booking_id = int(reservation_id_raw)
    except ValueError:
        return await _render_form(request, error=t("err.select_reservation"))

    try:
        starts_at, ends_at = _round_to_hours(starts_at_raw, ends_at_raw)
    except ValueError:
        return await _render_form(request, error=t("err.bad_datetime"))
    if ends_at <= starts_at:
        return await _render_form(request, error=t("err.end_before_start"))
    starts_at = _clamp_start(starts_at)
    if ends_at <= starts_at:
        return await _render_form(request, error=t("err.window_over"))

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
        return await _render_form(request, error=t("err.create_failed", exc=exc))

    log.info(
        "Early-checkin code created booking=%s window=%s→%s", booking_id, starts_at, ends_at
    )
    message = _compose_message(door_code.code, starts_at, ends_at, guest_language)
    return HTMLResponse(
        _result_page(
            t,
            code=door_code.code,
            guest_name=guest_name,
            property_name=property_name,
            starts_at=starts_at,
            ends_at=ends_at,
            booking_id=booking_id,
            message=message,
            language=guest_language,
            source=source,
        )
    )


@router.post("/early-checkin/send", response_class=HTMLResponse)
async def send_early_checkin(request: Request):
    """Send the (possibly edited) message to the guest on their booking."""
    t = translator_for(request)
    form = await request.form()
    reservation_id_raw = str(form.get("reservation_id", "")).strip()
    guest_name = str(form.get("guest_name", "")).strip()
    message = str(form.get("message", "")).strip()
    source = str(form.get("source", "")).strip() or SOURCE_BEDS24

    try:
        booking_id = int(reservation_id_raw)
    except ValueError:
        return HTMLResponse(_sent_error_page(t, t("err.missing_reservation")))
    if not message:
        return HTMLResponse(
            _send_result_page(t, booking_id, guest_name, message, source, error=t("err.empty_message"))
        )

    # Route the send back to the PMS the booking came from (Beds24 / Smoobu).
    gateway = _gateways(request).get(source)
    if gateway is None:
        return HTMLResponse(
            _send_result_page(t, booking_id, guest_name, message, source,
                              error=t("err.backend_not_configured", source=source))
        )
    try:
        await gateway.send_guest_message(booking_id, message)
    except BookingGatewayError as exc:
        log.error("Sending early-checkin message failed: %s", exc)
        return HTMLResponse(
            _send_result_page(t, booking_id, guest_name, message, source,
                              error=t("err.send_failed", exc=exc))
        )

    log.info("Early-checkin message sent on booking %s (%d chars)", booking_id, len(message))
    guest_label = html.escape(guest_name or t("early.the_guest"))
    content = f"""{brand(logo="📨", heading=t("early.sent_heading"))}
    <p class="success" style="text-align:center">{t("early.sent_body", name=f"<strong>{guest_label}</strong>")}</p>
    <p class="links"><a href="/early-checkin">{t("early.another_guest")}</a> · <a href="/door-codes">{t("nav.adhoc_code")}</a></p>"""
    return HTMLResponse(page(title=t("early.sent_title"), content=content, lang=t.lang))


def _send_form(t: Translator, booking_id: int, guest_name: str, message: str, language: str, source: str, error: str = "") -> str:
    """The editable message + Send button (reused on the result and on send failure)."""
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    label = t("early.msg_to", name=html.escape(guest_name or t("early.the_guest")), lang=_lang_label(t, language))
    return f"""{error_html}
    <form method="post" action="/early-checkin/send">
      <input type="hidden" name="reservation_id" value="{booking_id}" />
      <input type="hidden" name="guest_name" value="{html.escape(guest_name)}" />
      <input type="hidden" name="source" value="{html.escape(source)}" />
      <label for="message">{label}</label>
      <textarea id="message" name="message" rows="9">{html.escape(message)}</textarea>
      <button type="submit">{t("early.send_to_guest")}</button>
    </form>"""


def _result_page(
    t: Translator,
    *,
    code: str,
    guest_name: str,
    property_name: str,
    starts_at: str,
    ends_at: str,
    booking_id: int,
    message: str,
    language: str,
    source: str,
) -> str:
    content = f"""{brand(logo="✅", heading=t("common.code_created"))}
    {code_result(code, t)}
    <p class="meta">
      {f'{t("common.for")}: <strong>{html.escape(guest_name)}</strong><br>' if guest_name else ""}
      {f'{t("common.property_label")}: {html.escape(property_name)}<br>' if property_name else ""}
      {t("common.valid")}: {starts_at.replace("T", " ")[:16]} &rarr; {ends_at.replace("T", " ")[:16]}
    </p>
    {_send_form(t, booking_id, guest_name, message, language, source)}
    <p class="links"><a href="/early-checkin">{t("early.another_guest")}</a> · <a href="/door-codes">{t("nav.adhoc_code")}</a></p>"""
    return page(title=t("early.result_title"), content=content, max_width="460px", lang=t.lang)


def _send_result_page(t: Translator, booking_id: int, guest_name: str, message: str, source: str, *, error: str) -> str:
    """Shown when a send fails — keep the editable message so the owner can retry."""
    content = f"""{brand(logo="✉️", heading=t("early.send_heading"))}
    {_send_form(t, booking_id, guest_name, message, "", source, error=error)}
    <p class="links"><a href="/early-checkin">{t("early.start_over")}</a></p>"""
    return page(title=t("early.send_title"), content=content, max_width="460px", lang=t.lang)


def _sent_error_page(t: Translator, msg: str) -> str:
    content = f"""{brand(logo="⚠️", heading=t("early.couldnt_send"))}
    <p class="error">{html.escape(msg)}</p>
    <p class="links"><a href="/early-checkin">{t("early.back")}</a></p>"""
    return page(title=t("early.error_title"), content=content, lang=t.lang)
