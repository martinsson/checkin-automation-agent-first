"""
EmailCleanerNotifier.send_departure_notice — the optional owner BCC copy.

SMTP is mocked; we assert on the envelope recipients passed to sendmail.
"""

from unittest.mock import patch

import pytest

from src.communication.email_notifier import EmailCleanerNotifier
from src.ports.cleaner import DepartureNotice


def _notifier(**kw):
    base = dict(
        smtp_host="smtp", smtp_port=587, smtp_user="me@x.com", smtp_password="pw",
        imap_host="imap", imap_port=993, cleaner_email="cleaner@x.com",
    )
    base.update(kw)
    return EmailCleanerNotifier(**base)


def _notice():
    return DepartureNotice(
        property_name="Le Fernand", guest_name="Alice",
        departure_date="2026-08-01", summary_fr="Départ anticipé.",
    )


async def _send(notifier):
    with patch("src.communication.email_notifier.smtplib.SMTP") as SMTP:
        smtp = SMTP.return_value.__enter__.return_value
        await notifier.send_departure_notice("cleaner@x.com", _notice())
        from_addr, recipients, raw = smtp.sendmail.call_args[0]
        return recipients, raw


@pytest.mark.asyncio
async def test_copy_to_adds_silent_bcc():
    recipients, raw = await _send(_notifier(copy_to="owner@x.com"))
    assert recipients == ["cleaner@x.com", "owner@x.com"]  # owner gets a copy
    assert "owner@x.com" not in raw                         # …but not in the visible headers


@pytest.mark.asyncio
async def test_no_copy_when_blank():
    recipients, _ = await _send(_notifier(copy_to=""))
    assert recipients == ["cleaner@x.com"]


@pytest.mark.asyncio
async def test_copy_deduped_when_equal_to_cleaner():
    recipients, _ = await _send(_notifier(copy_to="cleaner@x.com"))
    assert recipients == ["cleaner@x.com"]  # not sent twice
