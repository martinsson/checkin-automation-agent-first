"""
Review UI routes — owner approves or rejects AI-generated draft replies.
"""

import logging

import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from src.web.layout import brand, page

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
        body = '<p class="empty">No pending drafts.</p>'
    else:
        items = []
        for d in drafts:
            # Load events to show context (guest request + cleaner email + cleaner reply)
            events = await memory.get_events(d.reservation_id)
            context_html = ""
            for e in events:
                p = e.payload
                if e.event_type == "hostbuddy_action_item":
                    context_html += f"""
<div class="ctx ctx--guest">
  <strong>Guest request</strong> ({html.escape(str(p.get('category', '')))})<br>
  Guest: {html.escape(str(p.get('guest_name', '')))} — Property: {html.escape(str(p.get('property_name', '')))}<br>
  <em>{html.escape(str(p.get('message_summary', '')))}</em>
</div>"""
                elif e.event_type == "cleaner_email_sent":
                    context_html += f"""
<div class="ctx ctx--out">
  <strong>Email sent to cleaner</strong> (date: {html.escape(str(p.get('date', '')))})<br>
  <pre>{html.escape(str(p.get('message', '')))}</pre>
</div>"""
                elif e.event_type == "cleaner_reply":
                    context_html += f"""
<div class="ctx ctx--reply">
  <strong>Cleaner reply</strong><br>
  <em>{html.escape(str(p.get('raw_text', '')))}</em>
</div>"""

            items.append(f"""
<div class="draft">
  <h3>Draft #{d.draft_id} · {html.escape(str(d.intent))} · {html.escape(str(d.step))}</h3>
  <div class="when">reservation {d.reservation_id} — {d.created_at.strftime('%Y-%m-%d %H:%M UTC')}</div>
  <h4>Context</h4>
  {context_html}
  <h4>Proposed reply to guest</h4>
  <pre class="reply">{html.escape(str(d.draft_body))}</pre>
  <div class="actions">
    <form method="post" action="/review/{d.draft_id}/approve">
      <button type="submit" class="inline">Approve &amp; Send</button>
    </form>
    <form method="post" action="/review/{d.draft_id}/reject">
      <input name="comment" placeholder="Why rejected?" />
      <button type="submit" class="inline danger">Reject</button>
    </form>
  </div>
</div>""")
        body = "\n".join(items)

    content = f"""{brand(logo="📋", heading="Pending Drafts")}
    {body}
    <p class="links"><a href="/door-codes">Create door code</a> · <a href="/logout">Logout</a></p>"""
    return HTMLResponse(page(title="Draft Review", content=content, max_width="720px"))


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
