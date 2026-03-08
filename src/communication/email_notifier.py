"""
Email-based CleanerNotifier implementation.

Sends query emails via SMTP (Gmail) and polls for replies via IMAP.
Request IDs are embedded in email subjects so replies can be matched.
"""

import email as email_lib
import email.policy
import logging
import smtplib
import uuid
from datetime import datetime, timezone
from email.mime.text import MIMEText

import imapclient

from src.ports.cleaner import CleanerNotifier, CleanerQuery, CleanerResponse

log = logging.getLogger(__name__)

# Subject tag format so we can identify our own threads
_SUBJECT_TAG = "[checkin-req:{request_id}]"
_IMAP_FOLDER = "INBOX"


def _make_subject(request_id: str, intent: str) -> str:
    tag = _SUBJECT_TAG.format(request_id=request_id)
    label = "early check-in" if intent == "early_checkin" else "late checkout"
    return f"{tag} Guest {label} request"


def _extract_request_id(subject: str) -> str | None:
    """Extract request_id from a subject line containing [checkin-req:xxx]."""
    import re
    m = re.search(r"\[checkin-req:([^\]]+)\]", subject)
    return m.group(1) if m else None


class EmailCleanerNotifier(CleanerNotifier):
    """
    Sends cleaner queries via SMTP and polls for replies via IMAP.

    The system email account (EMAIL_USER) sends to CLEANER_EMAIL.
    Replies land in the system inbox and are matched via Subject tag.
    """

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password: str,
        imap_host: str,
        imap_port: int,
        cleaner_email: str,
        anthropic_api_key: str | None = None,
    ):
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_user = smtp_user
        self._smtp_password = smtp_password
        self._imap_host = imap_host
        self._imap_port = imap_port
        self._cleaner_email = cleaner_email
        # Track which message UIDs we've already processed
        self._seen_uids: set[int] = set()

    async def send_query(self, query: CleanerQuery) -> str:
        """Send query email to cleaner. Returns Message-ID as tracking ID."""
        subject = _make_subject(query.request_id, query.request_type)
        body = self._build_body(query)
        message_id = f"<{uuid.uuid4().hex}@checkin-automation>"

        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = self._smtp_user
        msg["To"] = self._cleaner_email
        msg["Subject"] = subject
        msg["Message-ID"] = message_id

        try:
            with smtplib.SMTP(self._smtp_host, self._smtp_port) as smtp:
                smtp.starttls()
                smtp.login(self._smtp_user, self._smtp_password)
                smtp.sendmail(self._smtp_user, self._cleaner_email, msg.as_string())
            log.info(
                "Cleaner query email sent to %s subject=%r tracking=%s",
                self._cleaner_email, subject, message_id,
            )
        except Exception as exc:
            log.error("Failed to send cleaner email: %s", exc)
            raise

        return message_id

    async def poll_responses(self) -> list[CleanerResponse]:
        """Poll IMAP inbox for replies from the cleaner."""
        responses: list[CleanerResponse] = []
        try:
            with imapclient.IMAPClient(self._imap_host, port=self._imap_port, ssl=True) as client:
                client.login(self._smtp_user, self._smtp_password)
                client.select_folder(_IMAP_FOLDER, readonly=True)

                # Search for emails from the cleaner
                uids = client.search(["FROM", self._cleaner_email])
                new_uids = [u for u in uids if u not in self._seen_uids]

                if not new_uids:
                    return []

                messages = client.fetch(new_uids, ["ENVELOPE", "RFC822"])
                for uid, data in messages.items():
                    raw = data.get(b"RFC822", b"")
                    msg = email_lib.message_from_bytes(raw, policy=email_lib.policy.default)

                    subject = msg.get("Subject", "")
                    request_id = _extract_request_id(subject)

                    if request_id is None:
                        # Not one of our threads
                        self._seen_uids.add(uid)
                        continue

                    body = self._extract_body(msg)
                    responses.append(CleanerResponse(request_id=request_id, raw_text=body))
                    self._seen_uids.add(uid)
                    log.info(
                        "Received cleaner reply uid=%s request_id=%s",
                        uid, request_id,
                    )

        except Exception as exc:
            log.error("IMAP poll failed: %s", exc)

        return responses

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _build_body(query: CleanerQuery) -> str:
        lines = [
            f"Hi {query.cleaner_name},",
            "",
            f"We have a guest request for {query.property_name}.",
            "",
        ]
        if query.request_type == "early_checkin":
            lines.append(f"Guest {query.guest_name} would like to check in early on {query.date}.")
        else:
            lines.append(f"Guest {query.guest_name} would like to check out late on {query.date}.")

        if query.original_time:
            lines.append(f"Scheduled time: {query.original_time}")
        if query.requested_time:
            lines.append(f"Requested time: {query.requested_time}")

        lines += [
            "",
            query.message,
            "",
            "Could you let us know if this is possible? Please reply to this email.",
            "",
            "Thanks!",
        ]
        return "\n".join(lines)

    @staticmethod
    def _extract_body(msg) -> str:
        """Extract plain text body from an email message."""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    return part.get_content()
        else:
            if msg.get_content_type() == "text/plain":
                return msg.get_content()
        return msg.get_payload(decode=True).decode("utf-8", errors="replace")
