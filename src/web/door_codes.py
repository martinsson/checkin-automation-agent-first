"""
Door code form — owner creates an ad-hoc Igloohome access code
(handyman, early guest, ...) via the Make integration.
"""

import html
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.config.device_map import load_device_map
from src.ports.door_lock import DoorCodeRequest, DoorLockError
from src.web.layout import brand, code_result, page

log = logging.getLogger(__name__)

router = APIRouter()

# Igloohome codes are interpreted in the flats' local time; keep everything in
# Europe/Paris so the naive datetimes we send match what Make expects.
_TZ = ZoneInfo("Europe/Paris")


def _paris_now() -> datetime:
    return datetime.now(_TZ)


def _current_hour() -> datetime:
    """Now, floored to the full hour (naive, Europe/Paris)."""
    return _paris_now().replace(minute=0, second=0, microsecond=0, tzinfo=None)


def _default_window() -> tuple[str, str]:
    """Default validity: today 14:00 → tomorrow 12:00, but never a past start.

    In the evening, "today 14:00" is already past — Igloohome rejects a window
    that starts in the past — so the start is clamped up to the current hour.
    """
    now = _paris_now().replace(tzinfo=None)
    start = now.replace(hour=14, minute=0, second=0, microsecond=0)
    if start <= now:
        start = _current_hour()
    end = (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    fmt = "%Y-%m-%dT%H:%M"
    return start.strftime(fmt), end.strftime(fmt)


def _clamp_start(starts_at: str, now_hour: datetime | None = None) -> str:
    """Clamp a naive 'YYYY-MM-DDTHH:MM:SS' start up to the current hour if past.

    igloohome rejects a code whose window is already in the past; clamping the
    start to the current hour keeps an evening/late request valid ("from now").
    """
    floor = (now_hour or _current_hour()).strftime("%Y-%m-%dT%H:00:00")
    return floor if starts_at < floor else starts_at


def _form_page(
    *,
    starts_at: str,
    ends_at: str,
    person_name: str = "",
    property_name: str = "",
    error: str = "",
) -> str:
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""

    selected = property_name.strip().casefold()
    options = ['<option value="">— Default lock —</option>']
    for name in load_device_map().property_names:
        sel = " selected" if name.casefold() == selected else ""
        options.append(
            f'<option value="{html.escape(name)}"{sel}>{html.escape(name)}</option>'
        )
    property_options = "\n".join(options)

    content = f"""{brand(logo="🔐", heading="Create a door code",
                         subtitle="Temporary Igloohome code via Make")}
    {error_html}
    <form method="post" action="/door-codes">
      <label for="person_name">For whom (optional)</label>
      <input id="person_name" name="person_name" value="{html.escape(person_name)}"
             placeholder="e.g. Plombier Dupont — just a label" autofocus />
      <label for="property_name">Property</label>
      <select id="property_name" name="property_name">
        {property_options}
      </select>
      <label for="starts_at">Valid from</label>
      <input id="starts_at" type="datetime-local" name="starts_at" value="{starts_at}" required />
      <label for="ends_at">Valid until</label>
      <input id="ends_at" type="datetime-local" name="ends_at" value="{ends_at}" required />
      <p class="hint">Igloohome codes start and end on the hour — minutes are rounded
         (start down, end up).</p>
      <button type="submit">Create code</button>
    </form>
    <p class="links"><a href="/early-checkin">Early check-in</a> · <a href="/review">Drafts</a> · <a href="/logout">Logout</a></p>"""
    return page(title="Create Door Code", content=content)


def _round_to_hours(starts_at: str, ends_at: str) -> tuple[str, str]:
    """Round start down and end up to the full hour (Igloohome requirement)."""
    start = datetime.strptime(starts_at, "%Y-%m-%dT%H:%M")
    end = datetime.strptime(ends_at, "%Y-%m-%dT%H:%M")
    start = start.replace(minute=0)
    if end.minute > 0:
        end = end.replace(minute=0) + timedelta(hours=1)
    fmt = "%Y-%m-%dT%H:%M:00"
    return start.strftime(fmt), end.strftime(fmt)


@router.get("/door-codes", response_class=HTMLResponse)
async def door_code_form(request: Request):
    starts_at, ends_at = _default_window()
    return HTMLResponse(_form_page(starts_at=starts_at, ends_at=ends_at))


@router.post("/door-codes", response_class=HTMLResponse)
async def create_door_code(request: Request):
    form = await request.form()
    person_name = str(form.get("person_name", "")).strip()
    property_name = str(form.get("property_name", "")).strip()
    starts_at_raw = str(form.get("starts_at", "")).strip()
    ends_at_raw = str(form.get("ends_at", "")).strip()

    def form_with_error(message: str) -> HTMLResponse:
        default_start, default_end = _default_window()
        return HTMLResponse(
            _form_page(
                starts_at=starts_at_raw or default_start,
                ends_at=ends_at_raw or default_end,
                person_name=person_name,
                property_name=property_name,
                error=message,
            ),
            status_code=400,
        )

    door_lock = getattr(request.app.state, "door_lock", None)
    if door_lock is None:
        return form_with_error(
            "Door lock gateway is not configured (set MAKE_IGLOOHOME_WEBHOOK_URL)."
        )

    try:
        starts_at, ends_at = _round_to_hours(starts_at_raw, ends_at_raw)
    except ValueError:
        return form_with_error("Invalid date/time format.")

    if ends_at <= starts_at:
        return form_with_error("The end must be after the start.")

    # A start in the past makes Igloohome reject the whole window; clamp it to
    # the current hour so an evening/late request is still valid ("from now").
    starts_at = _clamp_start(starts_at)

    if ends_at <= starts_at:
        return form_with_error("That window is already over — pick an end time in the future.")

    # "For whom" is just a label; fall back to the property (or a generic tag)
    # so the lock app still shows something meaningful when it's left blank.
    code_name = person_name or property_name or "Code manuel"
    code_request = DoorCodeRequest(
        person_name=person_name,
        starts_at=starts_at,
        ends_at=ends_at,
        purpose="manual",
        property_name=property_name,
        device_id=load_device_map().device_for(property_name),
        code_name=code_name,
    )
    try:
        door_code = await door_lock.create_code(code_request)
    except DoorLockError as exc:
        log.error("Manual door code creation failed: %s", exc)
        return form_with_error(f"Code creation failed: {exc}")

    log.info(
        "Manual door code created for %r window=%s→%s code_id=%s",
        person_name, starts_at, ends_at, door_code.code_id,
    )
    content = f"""{brand(logo="✅", heading="Code created")}
    {code_result(door_code.code)}
    <p class="meta">
      {f"For: <strong>{html.escape(person_name)}</strong><br>" if person_name else ""}
      {f"Property: {html.escape(property_name)}<br>" if property_name else ""}
      Valid: {starts_at.replace("T", " ")[:16]} &rarr; {ends_at.replace("T", " ")[:16]}
    </p>
    <p class="links"><a href="/door-codes">Create another</a> · <a href="/review">Drafts</a></p>"""
    return HTMLResponse(page(title="Door Code Created", content=content))
