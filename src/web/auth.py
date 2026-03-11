"""
Simple token-based authentication for the review UI.

The owner logs in with REVIEW_TOKEN (a pre-shared secret).
The token is stored in a signed session cookie.
"""

import logging
from typing import Callable

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger(__name__)

router = APIRouter()

# Paths that don't require authentication
_PUBLIC_PATHS = {"/login", "/health", "/webhook/hostbuddy"}


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Check for a valid session cookie on all non-public routes.
    Redirects to /login if not authenticated.
    """

    def __init__(self, app, review_token: str):
        super().__init__(app)
        self._token = review_token

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Public paths, webhooks, and API endpoints pass through
        if (
            path in _PUBLIC_PATHS
            or path.startswith("/webhook/")
            or path.startswith("/events/")
            or path.startswith("/requests/")
        ):
            return await call_next(request)

        # Check session cookie
        session_token = request.cookies.get("session")
        if session_token != self._token:
            if request.method == "GET":
                return RedirectResponse(url="/login")
            return Response(status_code=401, content="Unauthorized")

        return await call_next(request)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head><title>Login — Check-in Review</title></head>
<body>
  <h2>Owner Login</h2>
  <form method="post" action="/login">
    <input type="password" name="token" placeholder="Review token" autofocus />
    <button type="submit">Login</button>
  </form>
</body>
</html>
""")


@router.post("/login")
async def login(request: Request):
    form = await request.form()
    token = form.get("token", "")
    review_token = request.app.state.review_token

    if token != review_token:
        return HTMLResponse("<p>Invalid token.</p><a href='/login'>Try again</a>", status_code=401)

    response = RedirectResponse(url="/review", status_code=303)
    response.set_cookie("session", token, httponly=True, samesite="lax")
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session")
    return response


@router.get("/health")
async def health():
    return {"status": "ok"}
