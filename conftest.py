"""Root conftest — ensures src/ is importable without pip install."""
import sys
import os

# Add project root to sys.path so `src.*` imports work when running pytest from repo root
sys.path.insert(0, os.path.dirname(__file__))

# Set test defaults before any src modules are imported (app.py creates app at module level).
# Use explicit check to override empty-string values that CI sets for unconfigured secrets.
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
    if not os.environ.get(_key):
        os.environ[_key] = _val
