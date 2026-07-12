"""
Review UI routes — owner approves or rejects AI-generated draft replies.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
async def index():
    """Root — send the owner to the default page (door codes)."""
    return RedirectResponse(url="/door-codes", status_code=303)


@router.get("/review", response_class=HTMLResponse)
async def review_list(request: Request):
    """List all pending drafts for owner review."""
    memory = request.app.state.memory
    drafts = await memory.get_pending_drafts()

    if not drafts:
        body = "<p>No pending drafts.</p>"
    else:
        items = []
        for d in drafts:
            # Load events to show context (guest request + cleaner email + cleaner reply)
            events = await memory.get_events(d.reservation_id)
            context_html = ""
            for e in events:
                if e.event_type == "hostbuddy_action_item":
                    p = e.payload
                    context_html += f"""
<div style="background:#e8f4fd;padding:0.8em;margin-bottom:0.5em;border-left:3px solid #2196F3">
  <strong>Guest request</strong> ({p.get('category', '')})<br>
  Guest: {p.get('guest_name', '')} — Property: {p.get('property_name', '')}<br>
  <em>{p.get('message_summary', '')}</em>
</div>"""
                elif e.event_type == "cleaner_email_sent":
                    p = e.payload
                    context_html += f"""
<div style="background:#fff3e0;padding:0.8em;margin-bottom:0.5em;border-left:3px solid #FF9800">
  <strong>Email sent to cleaner</strong> (date: {p.get('date', '')})<br>
  <pre style="margin:0.3em 0;white-space:pre-wrap">{p.get('message', '')}</pre>
</div>"""
                elif e.event_type == "cleaner_reply":
                    p = e.payload
                    context_html += f"""
<div style="background:#e8f5e9;padding:0.8em;margin-bottom:0.5em;border-left:3px solid #4CAF50">
  <strong>Cleaner reply</strong><br>
  <em>{p.get('raw_text', '')}</em>
</div>"""

            items.append(f"""
<div style="border:1px solid #ccc; margin:1em; padding:1em;">
  <strong>Draft #{d.draft_id}</strong> — reservation {d.reservation_id} — {d.intent} — {d.step}<br>
  <em>{d.created_at.strftime('%Y-%m-%d %H:%M UTC')}</em>
  <h4 style="margin:0.8em 0 0.3em">Context</h4>
  {context_html}
  <h4 style="margin:0.8em 0 0.3em">Proposed reply to guest</h4>
  <pre style="background:#f5f5f5;padding:1em">{d.draft_body}</pre>
  <form method="post" action="/review/{d.draft_id}/approve" style="display:inline">
    <button type="submit" style="background:green;color:white;padding:.5em 1em">Approve &amp; Send</button>
  </form>
  &nbsp;
  <form method="post" action="/review/{d.draft_id}/reject" style="display:inline">
    <input name="comment" placeholder="Why rejected?" style="width:300px" />
    <button type="submit" style="background:red;color:white;padding:.5em 1em">Reject</button>
  </form>
</div>""")
        body = "\n".join(items)

    return HTMLResponse(f"""
<!DOCTYPE html>
<html>
<head><title>Draft Review</title></head>
<body>
  <h2>Pending Drafts</h2>
  {body}
  <p><a href="/door-codes">Create door code</a> — <a href="/logout">Logout</a></p>
</body>
</html>
""")


@router.post("/review/{draft_id}/approve")
async def approve_draft(draft_id: int, request: Request):
    """Owner approves a draft — marks it ok for dispatch."""
    memory = request.app.state.memory
    draft = await memory.get_draft(draft_id)
    if draft is None:
        return JSONResponse({"error": "draft not found"}, status_code=404)

    await memory.review_draft(draft_id, "ok")
    log.info("Draft %d approved", draft_id)

    if request.headers.get("accept", "").startswith("application/json"):
        return JSONResponse({"status": "approved", "draft_id": draft_id})
    return RedirectResponse(url="/review", status_code=303)


@router.post("/review/{draft_id}/reject")
async def reject_draft(draft_id: int, request: Request):
    """Owner rejects a draft with an optional comment."""
    memory = request.app.state.memory
    draft = await memory.get_draft(draft_id)
    if draft is None:
        return JSONResponse({"error": "draft not found"}, status_code=404)

    form = await request.form()
    comment = form.get("comment", "")
    await memory.review_draft(draft_id, "nok", owner_comment=comment or None)
    log.info("Draft %d rejected — comment: %s", draft_id, comment)

    if request.headers.get("accept", "").startswith("application/json"):
        return JSONResponse({"status": "rejected", "draft_id": draft_id})
    return RedirectResponse(url="/review", status_code=303)


@router.get("/events/{reservation_id}")
async def event_log(reservation_id: int, request: Request):
    """Return the full agent event log for a reservation (JSON)."""
    memory = request.app.state.memory
    events = await memory.get_events(reservation_id)
    return JSONResponse([
        {
            "event_type": e.event_type,
            "payload": e.payload,
            "created_at": e.created_at.isoformat(),
        }
        for e in events
    ])


@router.get("/requests/{reservation_id}")
async def request_list(reservation_id: int, request: Request):
    """Return all requests for a reservation (JSON)."""
    memory = request.app.state.memory
    history = await memory.get_history(reservation_id)
    return JSONResponse([
        {
            "request_id": r.request_id,
            "intent": r.intent,
            "status": r.status.value if hasattr(r.status, "value") else r.status,
            "guest_name": r.guest_name,
            "property_name": r.property_name,
            "guest_message": r.guest_message,
        }
        for r in history
    ])
