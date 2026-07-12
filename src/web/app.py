"""
FastAPI application for draft review.

Start with:
    uvicorn src.web.app:app --host 0.0.0.0 --port 8001 --reload

Required environment variables:
    REVIEW_TOKEN  — pre-shared token for owner login
    DB_PATH       — path to SQLite database (default: data/checkin.db)
"""

import logging
import os
import sys

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()  # load .env before anything reads env vars

from src.adapters.sqlite_memory import SqliteRequestMemory
from src.web.auth import AuthMiddleware
from src.web.auth import router as auth_router
from src.web.routes import router as review_router
from src.web.hostbuddy_webhook import router as webhook_router
from src.web.contact import router as contact_router
from src.web.door_codes import router as door_codes_router

log = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable {name!r} is not set.")
    return value


def create_app() -> FastAPI:
    review_token = _require_env("REVIEW_TOKEN")
    db_path = os.environ.get("DB_PATH", "data/checkin.db")

    memory = SqliteRequestMemory(db_path)

    # Build the agent (imports deferred to avoid hard dep if agent module not needed)
    from src.agent import AgentRunner
    from src.communication.email_notifier import EmailCleanerNotifier

    cleaner_notifier = EmailCleanerNotifier(
        smtp_host=os.environ.get("EMAIL_SMTP_HOST", os.environ.get("SMTP_HOST", "")),
        smtp_port=int(os.environ.get("EMAIL_SMTP_PORT", os.environ.get("SMTP_PORT", "587"))),
        smtp_user=os.environ.get("EMAIL_USER", os.environ.get("SMTP_USER", "")),
        smtp_password=os.environ.get("EMAIL_PASSWORD", os.environ.get("SMTP_PASSWORD", "")),
        imap_host=os.environ.get("EMAIL_IMAP_HOST", os.environ.get("IMAP_HOST", "")),
        imap_port=int(os.environ.get("EMAIL_IMAP_PORT", os.environ.get("IMAP_PORT", "993"))),
        cleaner_email=os.environ.get("CLEANER_EMAIL", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
        dry_run=os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"),
    )
    door_lock = None
    make_webhook_url = os.environ.get("MAKE_IGLOOHOME_WEBHOOK_URL", "").strip()
    if make_webhook_url:
        from src.adapters.make_door_lock import MakeDoorLockGateway

        door_lock = MakeDoorLockGateway(
            webhook_url=make_webhook_url,
            api_key=os.environ.get("MAKE_IGLOOHOME_API_KEY", "").strip(),
        )

    agent = AgentRunner(memory=memory, cleaner_notifier=cleaner_notifier, door_lock=door_lock)

    application = FastAPI(title="Checkin Review", docs_url=None, redoc_url=None)
    application.state.review_token = review_token
    application.state.memory = memory
    application.state.agent = agent
    application.state.door_lock = door_lock

    application.add_middleware(AuthMiddleware, review_token=review_token)
    application.include_router(auth_router)
    application.include_router(review_router)
    application.include_router(webhook_router)
    application.include_router(contact_router)
    application.include_router(door_codes_router)

    log.info("Web UI started. DB: %s", db_path)
    return application


app = create_app()
