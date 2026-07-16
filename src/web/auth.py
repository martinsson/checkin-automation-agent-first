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

from src.web.i18n import (
    DEFAULT_LANG,
    LANG_COOKIE,
    LANG_COOKIE_MAX_AGE,
    normalize_lang,
    translator_for,
)
from src.web.layout import brand, page

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
            or path.startswith("/lang/")  # language switch works before login too
        ):
            return await call_next(request)

        # Check session cookie
        session_token = request.cookies.get("session")
        if session_token != self._token:
            if request.method == "GET":
                return RedirectResponse(url="/login")
            return Response(status_code=401, content="Unauthorized")

        return await call_next(request)


def _login_page(t, *, error: str = "", username: str = "") -> str:
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    content = f"""{brand(logo="🔑", heading=t("login.heading"), subtitle=t("login.subtitle"))}
    {error_html}
    <form method="post" action="/login">
      <label for="username">{t("login.user")}</label>
      <input id="username" name="username" type="text" autocomplete="username"
             value="{html.escape(username)}" autocapitalize="none" autofocus required />
      <label for="password">{t("login.password")}</label>
      <input id="password" name="password" type="password"
             autocomplete="current-password" required />
      <button type="submit">{t("login.submit")}</button>
    </form>"""
    return page(title=t("login.title"), content=content, max_width="360px", lang=t.lang)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return HTMLResponse(_login_page(translator_for(request)))


@router.get("/lang/{code}")
async def set_language(code: str, request: Request):
    """Set the UI-language cookie and bounce back to the page the owner came
    from (same-origin Referer only, else the home page)."""
    lang = normalize_lang(code) or DEFAULT_LANG
    referer = request.headers.get("referer", "")
    next_url = "/"
    if referer:
        # Keep only the local path+query so we never redirect off-site.
        from urllib.parse import urlsplit

        parts = urlsplit(referer)
        if parts.path.startswith("/"):
            next_url = parts.path + (f"?{parts.query}" if parts.query else "")
    response = RedirectResponse(url=next_url, status_code=303)
    response.set_cookie(
        LANG_COOKIE,
        lang,
        max_age=LANG_COOKIE_MAX_AGE,
        samesite="lax",
    )
    return response


@router.post("/login")
async def login(request: Request):
    t = translator_for(request)
    form = await request.form()
    username = str(form.get("username", "")).strip().casefold()
    password = str(form.get("password", ""))
    review_token = request.app.state.review_token

    if username not in _USERS or password != review_token:
        return HTMLResponse(
            _login_page(t, error=t("login.error"), username=username),
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
