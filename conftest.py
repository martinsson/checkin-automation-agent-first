"""Root conftest — ensures src/ is importable without pip install."""
import sys
import os

# Add project root to sys.path so `src.*` imports work when running pytest from repo root
sys.path.insert(0, os.path.dirname(__file__))
