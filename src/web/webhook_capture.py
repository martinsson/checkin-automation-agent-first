"""
Raw webhook capture middleware.

Taps every ``POST /webhook/*`` delivery and stores it verbatim — timestamp,
path, method, headers, and the raw body — *before* any routing, parsing, or
validation runs. The point is reverse-engineering: HostBuddy's action-item
payload schema isn't documented, and different action-item categories (early
check-in, late checkout, "guest left", early-departure detection, …) may carry
subtly different fields. Capturing the real deliveries lets us compare their
shapes side by side and finalise the parsing.

Design constraints:
  * Never break a webhook. Capture is wrapped so any storage error is swallowed
    and the request proceeds untouched.
  * Read-once safe. The ASGI body stream can only be consumed once, so we buffer
    the ``http.request`` messages and replay them to the downstream app.
  * Only ``POST`` under ``/webhook/`` is captured — so we never log login form
    posts or other bodies that may contain the review token.

This is a pure-ASGI middleware (not BaseHTTPMiddleware) so it can buffer and
replay the receive stream cleanly.
"""

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

router = APIRouter()

# Header names we never want to persist verbatim (avoid storing secrets).
_REDACT_HEADERS = {"authorization", "cookie", "proxy-authorization"}
# Body larger than this is truncated before storage (a marker is appended).
_MAX_BODY = 64 * 1024


class WebhookCaptureMiddleware:
    """Persist a verbatim copy of every POST under ``path_prefix``."""

    def __init__(self, app, path_prefix: str = "/webhook/"):
        self.app = app
        self.path_prefix = path_prefix

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or not scope.get("path", "").startswith(self.path_prefix)
        ):
            await self.app(scope, receive, send)
            return

        # Buffer the whole request body, keeping the original messages so we can
        # replay them verbatim to the downstream app.
        messages = []
        body = b""
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.request":
                body += message.get("body", b"")
                if not message.get("more_body", False):
                    break
            else:  # e.g. http.disconnect
                break

        try:
            await self._capture(scope, body)
        except Exception as exc:  # capture must never break the webhook
            log.warning("webhook capture failed for %s — %s", scope.get("path"), exc)

        sent = False

        async def replay_receive():
            nonlocal sent
            if not sent:
                sent = True
                # Hand back the buffered messages as one coalesced body chunk.
                return {
                    "type": "http.request",
                    "body": body,
                    "more_body": False,
                }
            return await receive()

        await self.app(scope, replay_receive, send)

    async def _capture(self, scope, body: bytes) -> None:
        app = scope.get("app")
        memory = getattr(getattr(app, "state", None), "memory", None)
        if memory is None:
            return

        headers = {
            key.decode("latin-1").lower(): (
                "<redacted>"
                if key.decode("latin-1").lower() in _REDACT_HEADERS
                else value.decode("latin-1")
            )
            for key, value in scope.get("headers", [])
        }

        text = body.decode("utf-8", errors="replace")
        if len(text) > _MAX_BODY:
            text = text[:_MAX_BODY] + f"\n…[truncated {len(text) - _MAX_BODY} chars]"

        await memory.log_webhook_capture(
            path=scope.get("path", ""),
            method=scope.get("method", ""),
            headers=headers,
            body=text,
        )
        log.info(
            "webhook capture: %s %s (%d bytes, category=%s)",
            scope.get("method"),
            scope.get("path"),
            len(body),
            _peek_category(text),
        )


def _peek_category(text: str) -> str:
    """Best-effort pull of a `category` field out of a JSON body for the log
    line — purely informational, never raises."""
    parsed = _try_json(text)
    if isinstance(parsed, dict):
        return str(parsed.get("category", "?"))
    return "?"


def _try_json(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Viewer — GET /captures (behind AuthMiddleware; NOT under /webhook/, which
# bypasses login, because captured bodies contain guest PII).
# ---------------------------------------------------------------------------


@router.get("/captures")
async def list_captures(request: Request):
    """Recent raw webhook deliveries, newest first, for eyeballing payload shapes.

    Query params: ``?limit=N`` (default 50) and ``?path=/webhook/hostbuddy`` to
    filter by endpoint. Each capture includes the raw body plus a parsed
    ``body_json`` when the body is valid JSON, so different HostBuddy
    action-item categories can be compared field-by-field.
    """
    memory = request.app.state.memory
    try:
        limit = min(max(int(request.query_params.get("limit", "50")), 1), 500)
    except ValueError:
        limit = 50
    path_filter = request.query_params.get("path")

    captures = await memory.get_webhook_captures(limit=limit)
    items = []
    for cap in captures:
        if path_filter and cap.path != path_filter:
            continue
        items.append(
            {
                "id": cap.id,
                "at": cap.created_at.isoformat(),
                "method": cap.method,
                "path": cap.path,
                "category": _peek_category(cap.body),
                "headers": cap.headers,
                "body": cap.body,
                "body_json": _try_json(cap.body),
            }
        )
    return JSONResponse({"count": len(items), "captures": items})
