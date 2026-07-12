"""
Door code form — owner creates an ad-hoc Igloohome access code
(handyman, early guest, ...) via the Make integration.
"""

import html
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.config.device_map import load_device_map
from src.ports.door_lock import DoorCodeRequest, DoorLockError

log = logging.getLogger(__name__)

router = APIRouter()


def _default_window() -> tuple[str, str]:
    """Default validity: today 14:00 → tomorrow 12:00 (datetime-local format)."""
    today = datetime.now().replace(hour=14, minute=0, second=0, microsecond=0)
    tomorrow = (today + timedelta(days=1)).replace(hour=12)
    fmt = "%Y-%m-%dT%H:%M"
    return today.strftime(fmt), tomorrow.strftime(fmt)


def _form_page(
    *,
    starts_at: str,
    ends_at: str,
    person_name: str = "",
    property_name: str = "",
    error: str = "",
) -> str:
    error_html = (
        f'<p style="background:#fdecea;padding:0.8em;border-left:3px solid #f44336">'
        f"{html.escape(error)}</p>"
        if error
        else ""
    )

    selected = property_name.strip().casefold()
    options = ['<option value="">— Default lock —</option>']
    for name in load_device_map().property_names:
        sel = " selected" if name.casefold() == selected else ""
        options.append(
            f'<option value="{html.escape(name)}"{sel}>{html.escape(name)}</option>'
        )
    property_options = "\n".join(options)

    return f"""
<!DOCTYPE html>
<html>
<head><title>Create Door Code</title></head>
<body>
  <h2>Create a door code</h2>
  <p>Creates a temporary Igloohome code via Make (handyman, early guest, ...).</p>
  {error_html}
  <form method="post" action="/door-codes" style="max-width:420px">
    <p>
      <label>For whom (optional)<br>
        <input name="person_name" value="{html.escape(person_name)}"
               placeholder="e.g. Plombier Dupont — just a label" style="width:100%" autofocus />
      </label>
    </p>
    <p>
      <label>Property<br>
        <select name="property_name" style="width:100%">
          {property_options}
        </select>
      </label>
    </p>
    <p>
      <label>Valid from<br>
        <input type="datetime-local" name="starts_at" value="{starts_at}" required />
      </label>
    </p>
    <p>
      <label>Valid until<br>
        <input type="datetime-local" name="ends_at" value="{ends_at}" required />
      </label>
    </p>
    <p style="color:#666;font-size:0.9em">Igloohome codes start and end on the hour —
       minutes are rounded (start down, end up).</p>
    <button type="submit" style="background:green;color:white;padding:.5em 1em">
      Create code
    </button>
  </form>
  <p><a href="/review">Back to drafts</a> — <a href="/logout">Logout</a></p>
</body>
</html>
"""


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
    return HTMLResponse(f"""
<!DOCTYPE html>
<html>
<head><title>Door Code Created</title></head>
<body>
  <h2>Code created ✓</h2>
  <p style="font-size:2.5em;letter-spacing:0.15em;background:#e8f5e9;
            padding:0.5em;display:inline-block;border-left:3px solid #4CAF50">
    <strong>{html.escape(door_code.code)}</strong>
  </p>
  <p>
    {f"For: <strong>{html.escape(person_name)}</strong><br>" if person_name else ""}
    {f"Property: {html.escape(property_name)}<br>" if property_name else ""}
    Valid: {starts_at.replace("T", " ")[:16]} &rarr; {ends_at.replace("T", " ")[:16]}
  </p>
  <p><a href="/door-codes">Create another code</a> — <a href="/review">Back to drafts</a></p>
</body>
</html>
""")
