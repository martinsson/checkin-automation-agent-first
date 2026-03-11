"""Root conftest — ensures src/ is importable without pip install."""
import sys
import os

# Add project root to sys.path so `src.*` imports work when running pytest from repo root
sys.path.insert(0, os.path.dirname(__file__))

# Set test defaults before any src modules are imported (app.py creates app at module level)
os.environ.setdefault("REVIEW_TOKEN", "test-token")
os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("SMTP_HOST", "localhost")
os.environ.setdefault("SMTP_USER", "test@test.com")
os.environ.setdefault("SMTP_PASSWORD", "x")
os.environ.setdefault("IMAP_HOST", "localhost")
os.environ.setdefault("CLEANER_EMAIL", "cleaner@test.com")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
