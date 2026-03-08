#!/usr/bin/env bash
# Run inside a GitHub Codespace to start the review server.
# All secrets below must be set as Codespace secrets on GitHub.
#
# Set secrets at:
#   https://github.com/settings/codespaces
#   (or per-repo under Settings → Secrets and variables → Codespaces)
#
# Required secrets:
#   REVIEW_TOKEN          — pre-shared token to log in to /review
#   ANTHROPIC_API_KEY     — Claude API key
#   EMAIL_USER            — Gmail address that sends/receives cleaner emails
#   EMAIL_PASSWORD        — App password for EMAIL_USER (Gmail → App Passwords)
#   CLEANER_EMAIL         — Cleaner's email address
#   EMAIL_SMTP_HOST       — smtp.gmail.com
#   EMAIL_SMTP_PORT       — 587
#   EMAIL_IMAP_HOST       — imap.gmail.com
#   EMAIL_IMAP_PORT       — 993
#   CLEANER_NAME          — First name of the cleaner (e.g. Virginie)
#
# Optional:
#   DB_PATH               — path to SQLite file (default: data/checkin.db)
#   SMOOBU_API_KEY        — Smoobu API key (needed for daemon/poll cycle)
#   SMOOBU_APARTMENT_ID   — Smoobu apartment ID

set -e

cd "$(dirname "$0")/.."

# Create data directory if missing
mkdir -p data

# Validate required secrets are present
required=(REVIEW_TOKEN ANTHROPIC_API_KEY EMAIL_USER EMAIL_PASSWORD CLEANER_EMAIL)
missing=()
for var in "${required[@]}"; do
  if [[ -z "${!var}" ]]; then
    missing+=("$var")
  fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "ERROR: Missing Codespace secrets: ${missing[*]}"
  echo "Set them at: https://github.com/settings/codespaces"
  exit 1
fi

echo "All required secrets found. Starting server on port 8001..."
exec uvicorn src.web.app:app --host 0.0.0.0 --port 8001 --log-level info
