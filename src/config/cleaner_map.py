"""
Beds24 propertyId → cleaner contact resolution (for departure notifications).

Backed by config/cleaners.yaml. A property with no mapped cleaner (or a mapped
entry whose email is still a blank TODO placeholder) falls back to a default
address so a notification is never dropped for lack of routing.
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "cleaners.yaml"


@dataclass(frozen=True)
class CleanerContact:
    name: str
    email: str


class CleanerMap:
    """propertyId → CleanerContact, with a default fallback contact."""

    def __init__(
        self,
        contacts: dict[int, CleanerContact] | None = None,
        default_email: str = "",
        default_name: str = "Équipe ménage",
    ):
        self._by_id: dict[int, CleanerContact] = dict(contacts or {})
        self._default = CleanerContact(name=default_name, email=default_email)

    def for_property(self, property_id: int | str | None) -> CleanerContact:
        """Cleaner for this propertyId, falling back to the default contact.

        Falls back when the property is unmapped OR when its mapped email is
        blank (an un-filled TODO placeholder in the YAML)."""
        try:
            pid = int(property_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return self._default
        contact = self._by_id.get(pid)
        if contact is None or not contact.email.strip():
            if contact is not None:
                log.warning(
                    "Cleaner map: property %s has no email yet — using default %r",
                    pid, self._default.email,
                )
            return self._default
        return contact

    @classmethod
    def from_yaml(
        cls,
        path: str | Path | None = None,
        *,
        default_email: str = "",
        default_name: str = "Équipe ménage",
    ) -> "CleanerMap":
        p = Path(path) if path else _DEFAULT_PATH
        try:
            data = yaml.safe_load(p.read_text()) or {}
        except FileNotFoundError:
            log.warning("Cleaner map %s not found — all properties use the default", p)
            return cls(default_email=default_email, default_name=default_name)

        contacts: dict[int, CleanerContact] = {}
        for pid, entry in (data.get("properties") or {}).items():
            try:
                key = int(pid)
            except (TypeError, ValueError):
                log.warning("Cleaner map: skipping non-integer propertyId %r", pid)
                continue
            entry = entry or {}
            contacts[key] = CleanerContact(
                name=str(entry.get("name") or "").strip() or default_name,
                email=str(entry.get("email") or "").strip(),
            )
        return cls(contacts, default_email=default_email, default_name=default_name)


@functools.lru_cache(maxsize=1)
def load_cleaner_map(default_email: str = "", default_name: str = "Équipe ménage") -> CleanerMap:
    """Process-wide cached cleaner map loaded from the default YAML path."""
    return CleanerMap.from_yaml(default_email=default_email, default_name=default_name)
