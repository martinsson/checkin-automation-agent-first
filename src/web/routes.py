"""
Review UI routes — owner approves or rejects AI-generated draft replies.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

log = logging.getLogger(__name__)

router = APIRouter()


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
            items.append(f"""
<div style="border:1px solid #ccc; margin:1em; padding:1em;">
  <strong>Draft #{d.draft_id}</strong> — reservation {d.reservation_id} — {d.intent} — {d.step}<br>
  <em>{d.created_at.strftime('%Y-%m-%d %H:%M UTC')}</em>
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
  <p><a href="/logout">Logout</a></p>
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
