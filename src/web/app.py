"""
FastAPI application for draft review.

Start with:
    uvicorn src.web.app:app --host 0.0.0.0 --port 8000

Required environment variables:
    REVIEW_TOKEN  — pre-shared token for owner login
    DB_PATH       — path to SQLite database (default: data/checkin.db)
"""

import logging
import os
import sys

from fastapi import FastAPI

from src.adapters.sqlite_memory import SqliteRequestMemory
from src.web.auth import AuthMiddleware
from src.web.auth import router as auth_router
from src.web.routes import router as review_router
from src.web.hostbuddy_webhook import router as webhook_router

log = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"ERROR: environment variable {name!r} is not set.", file=sys.stderr)
        sys.exit(1)
    return value


def create_app() -> FastAPI:
    review_token = _require_env("REVIEW_TOKEN")
    db_path = os.environ.get("DB_PATH", "data/checkin.db")

    memory = SqliteRequestMemory(db_path)

    # Build the agent (imports deferred to avoid hard dep if agent module not needed)
    from src.agent import AgentRunner
    from src.communication.email_notifier import EmailCleanerNotifier

    cleaner_notifier = EmailCleanerNotifier(
        smtp_host=os.environ.get("SMTP_HOST", ""),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        smtp_user=os.environ.get("SMTP_USER", ""),
        smtp_password=os.environ.get("SMTP_PASSWORD", ""),
        imap_host=os.environ.get("IMAP_HOST", ""),
        imap_port=int(os.environ.get("IMAP_PORT", "993")),
        cleaner_email=os.environ.get("CLEANER_EMAIL", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
    )
    agent = AgentRunner(memory=memory, cleaner_notifier=cleaner_notifier)

    application = FastAPI(title="Checking Review", docs_url=None, redoc_url=None)
    application.state.review_token = review_token
    application.state.memory = memory
    application.state.agent = agent

    application.add_middleware(AuthMiddleware, review_token=review_token)
    application.include_router(auth_router)
    application.include_router(review_router)
    application.include_router(webhook_router)

    log.info("Web UI started. DB: %s", db_path)
    return application


app = create_app()
