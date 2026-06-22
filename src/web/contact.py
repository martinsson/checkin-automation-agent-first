"""
Public contact-form endpoint for the Cocon Grenoble marketing site.

The static site (served by Caddy at rental.changit.fr/cocon/) POSTs the
contact form here; this self-hosted endpoint emails the message to the owner
via SMTP, reusing the existing EMAIL_* configuration. No third-party form
service — visitor data stays on our own EU server.

Route: POST /cocon/api/contact  (kept public in AuthMiddleware)
"""

import logging
import os
import re
import smtplib
import uuid
from email.mime.text import MIMEText

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_RECIPIENT = os.environ.get("CONTACT_RECIPIENT", "martinsson.rouveyrol@gmail.com")
_MAX = {"name": 120, "email": 200, "message": 5000}


def _send_email(name: str, email: str, message: str) -> None:
    host = os.environ.get("EMAIL_SMTP_HOST", os.environ.get("SMTP_HOST", ""))
    port = int(os.environ.get("EMAIL_SMTP_PORT", os.environ.get("SMTP_PORT", "587")))
    user = os.environ.get("EMAIL_USER", os.environ.get("SMTP_USER", ""))
    password = os.environ.get("EMAIL_PASSWORD", os.environ.get("SMTP_PASSWORD", ""))
    if not (host and user and password):
        raise RuntimeError("SMTP not configured (EMAIL_SMTP_HOST/EMAIL_USER/EMAIL_PASSWORD)")

    body = (
        f"Nouveau message depuis le site Cocon Grenoble\n\n"
        f"Nom    : {name}\n"
        f"Email  : {email}\n\n"
        f"Message :\n{message}\n"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = f"Cocon Grenoble <{user}>"
    msg["To"] = _RECIPIENT
    msg["Reply-To"] = email
    msg["Subject"] = f"Cocon Grenoble — message de {name}"
    msg["Message-ID"] = f"<{uuid.uuid4().hex}@cocon-grenoble>"

    with smtplib.SMTP(host, port) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.sendmail(user, [_RECIPIENT], msg.as_string())


@router.post("/cocon/api/contact")
async def contact(
    name: str = Form(""),
    email: str = Form(""),
    message: str = Form(""),
    botcheck: str = Form(""),
):
    # Honeypot: bots fill the hidden field — pretend success, send nothing.
    if botcheck:
        return JSONResponse({"success": True})

    name = name.strip()[: _MAX["name"]]
    email = email.strip()[: _MAX["email"]]
    message = message.strip()[: _MAX["message"]]

    if not name or not message or not _EMAIL_RE.match(email):
        return JSONResponse(
            {"success": False, "message": "Champs invalides."}, status_code=422
        )

    try:
        _send_email(name, email, message)
    except Exception as exc:  # noqa: BLE001 — surface a generic error to the client
        log.error("Contact form email failed: %s", exc)
        return JSONResponse(
            {"success": False, "message": "Envoi impossible pour le moment."},
            status_code=502,
        )

    log.info("Contact form message sent from %s <%s>", name, email)
    return JSONResponse({"success": True})
