#!/usr/bin/env bash
# Import all secrets from .env into GitHub Codespace secrets for this repo.
#
# Requirements:
#   - gh CLI installed and authenticated (`gh auth login`)
#   - .env file present at the repo root (it is gitignored)
#
# Usage:
#   bash scripts/set-codespace-secrets.sh

set -e

REPO="martinsson/checkin-automation-agent-first"
ENV_FILE="$(dirname "$0")/../.env"

if ! command -v gh &>/dev/null; then
  echo "ERROR: gh CLI not found. Install from https://cli.github.com"
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env file not found at $ENV_FILE"
  exit 1
fi

echo "Importing secrets from .env into Codespace secrets for $REPO ..."

gh secret set \
  --app codespaces \
  --env-file "$ENV_FILE" \
  --repos "$REPO"

echo "Done. Verify at: https://github.com/$REPO/settings/secrets/codespaces"
