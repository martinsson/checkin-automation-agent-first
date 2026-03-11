"""Root conftest — ensures src/ is importable without pip install."""
import sys
import os

# Add project root to sys.path so `src.*` imports work when running pytest from repo root
sys.path.insert(0, os.path.dirname(__file__))

# Provide safe test defaults for any required env vars so that module-level
# code in src/web/app.py (app = create_app()) doesn't blow up during collection
# when real secrets are absent (e.g. in CI without configured repository secrets).
_TEST_DEFAULTS = {
    "REVIEW_TOKEN": "test-token",
    "ANTHROPIC_API_KEY": "test-key",
    "EMAIL_USER": "test@example.com",
    "EMAIL_PASSWORD": "x",
    "EMAIL_SMTP_HOST": "localhost",
    "EMAIL_SMTP_PORT": "587",
    "EMAIL_IMAP_HOST": "localhost",
    "EMAIL_IMAP_PORT": "993",
    "CLEANER_EMAIL": "cleaner@example.com",
    "CLEANER_NAME": "Test Cleaner",
    "DB_PATH": ":memory:",
    "DRY_RUN": "true",
}
for _key, _val in _TEST_DEFAULTS.items():
    if not os.environ.get(_key):   # covers both missing AND empty-string (CI secrets not configured)
        os.environ[_key] = _val
