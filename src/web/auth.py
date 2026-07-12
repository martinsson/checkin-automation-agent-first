"""
Simple token-based authentication for the review UI.

The owner logs in with REVIEW_TOKEN (a pre-shared secret).
The token is stored in a signed session cookie.
"""

import html
import logging
from typing import Callable

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger(__name__)

router = APIRouter()

# Paths that don't require authentication
_PUBLIC_PATHS = {"/login", "/health", "/webhook/hostbuddy"}

# Known owners. Both share the same password (the REVIEW_TOKEN); the username
# exists so browsers offer to remember the login as a normal credential pair.
_USERS = {"johan", "aurelia"}

# Keep owners logged in for a year so they rarely have to re-authenticate.
_SESSION_MAX_AGE = 60 * 60 * 24 * 365


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
            or path.startswith("/cocon/api/")
        ):
            return await call_next(request)

        # Check session cookie
        session_token = request.cookies.get("session")
        if session_token != self._token:
            if request.method == "GET":
                return RedirectResponse(url="/login")
            return Response(status_code=401, content="Unauthorized")

        return await call_next(request)


def _login_page(*, error: str = "", username: str = "") -> str:
    error_html = (
        f'<p class="error">{error}</p>' if error else ""
    )
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Login — Check-in</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: linear-gradient(135deg, #1e3a5f 0%, #2d6a4f 100%);
      color: #1a1a1a;
      padding: 1.5rem;
    }}
    .card {{
      background: #fff;
      width: 100%;
      max-width: 360px;
      padding: 2.25rem 2rem;
      border-radius: 14px;
      box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
    }}
    .brand {{
      text-align: center;
      margin-bottom: 1.75rem;
    }}
    .brand .logo {{ font-size: 2.25rem; line-height: 1; }}
    .brand h1 {{
      font-size: 1.15rem;
      margin: 0.5rem 0 0.15rem;
      font-weight: 600;
    }}
    .brand p {{ margin: 0; color: #6b7280; font-size: 0.85rem; }}
    label {{
      display: block;
      font-size: 0.8rem;
      font-weight: 600;
      color: #374151;
      margin-bottom: 0.35rem;
    }}
    input {{
      width: 100%;
      padding: 0.7rem 0.8rem;
      margin-bottom: 1.1rem;
      border: 1px solid #d1d5db;
      border-radius: 8px;
      font-size: 1rem;
      transition: border-color 0.15s, box-shadow 0.15s;
    }}
    input:focus {{
      outline: none;
      border-color: #2d6a4f;
      box-shadow: 0 0 0 3px rgba(45, 106, 79, 0.15);
    }}
    button {{
      width: 100%;
      padding: 0.75rem;
      border: none;
      border-radius: 8px;
      background: #2d6a4f;
      color: #fff;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.15s;
    }}
    button:hover {{ background: #245a41; }}
    .error {{
      background: #fdecea;
      color: #b3261e;
      border-left: 3px solid #f44336;
      padding: 0.7rem 0.8rem;
      border-radius: 6px;
      font-size: 0.88rem;
      margin: 0 0 1.1rem;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="brand">
      <div class="logo">🔑</div>
      <h1>Check-in Console</h1>
      <p>Owner sign-in</p>
    </div>
    {error_html}
    <form method="post" action="/login">
      <label for="username">User</label>
      <input id="username" name="username" type="text" autocomplete="username"
             value="{html.escape(username)}" autocapitalize="none" autofocus required />
      <label for="password">Password</label>
      <input id="password" name="password" type="password"
             autocomplete="current-password" required />
      <button type="submit">Sign in</button>
    </form>
  </div>
</body>
</html>
"""


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(_login_page())


@router.post("/login")
async def login(request: Request):
    form = await request.form()
    username = str(form.get("username", "")).strip().casefold()
    password = str(form.get("password", ""))
    review_token = request.app.state.review_token

    if username not in _USERS or password != review_token:
        return HTMLResponse(
            _login_page(error="Wrong user or password.", username=username),
            status_code=401,
        )

    response = RedirectResponse(url="/door-codes", status_code=303)
    response.set_cookie(
        "session",
        review_token,
        max_age=_SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session")
    return response


@router.get("/health")
async def health():
    return {"status": "ok"}
