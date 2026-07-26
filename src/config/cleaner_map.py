"""
Property → cleaner routing for departure notifications.

Three layers, kept deliberately separate so each is owned by the right person:

  * WHO cleans WHICH property — ``assignments:`` in config/cleaners.yaml, keyed by
    the property display name. This is the humanly-edited part (an operator maps
    a property "enum" to a cleaner "enum").
  * property name ↔ Beds24 propertyId — the SAME map the /early-checkin form uses
    (config/beds24_properties.yaml via :class:`PropertyMap`). Not duplicated here.
  * cleaner key → email/name — the ``cleaners:`` registry in config/cleaners.yaml.

Resolution prefers the property NAME carried on the webhook payload (so routing
works even when Beds24 enrichment is unavailable, e.g. no API token), and falls
back to the propertyId from enrichment. Anything unmapped — or mapped to a
cleaner whose email is still a blank TODO — falls back to a default address so a
notification is never dropped for lack of routing.
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from src.config.property_map import PropertyMap, load_property_map

log = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "cleaners.yaml"


@dataclass(frozen=True)
class CleanerContact:
    name: str
    email: str


class CleanerMap:
    """Resolve a property (by name or Beds24 id) to its cleaner contact."""

    def __init__(
        self,
        assignments: dict[str, str] | None = None,
        cleaners: dict[str, CleanerContact] | None = None,
        property_map: PropertyMap | None = None,
        default_email: str = "",
        default_name: str = "Équipe ménage",
    ):
        # property display name (case-folded) → cleaner key
        self._assignments = {
            str(name).strip().casefold(): str(key).strip()
            for name, key in (assignments or {}).items()
            if str(name).strip() and str(key).strip()
        }
        # cleaner key → contact
        self._cleaners = dict(cleaners or {})
        self._property_map = property_map if property_map is not None else PropertyMap()
        self._default = CleanerContact(name=default_name, email=default_email)

    # -- resolution -----------------------------------------------------------

    def resolve(
        self, property_name: str = "", property_id: int | str | None = None
    ) -> CleanerContact:
        """Cleaner for this property. Name (from the payload) is primary; the
        Beds24 propertyId (from enrichment) is the fallback."""
        if property_name:
            contact = self._by_name(property_name)
            if contact is not None:
                return contact
        if property_id is not None:
            name = self._property_map.name_for(property_id)
            if name:
                contact = self._by_name(name)
                if contact is not None:
                    return contact
        return self._default

    def for_property_name(self, property_name: str) -> CleanerContact:
        return self._by_name(property_name) or self._default

    def for_property(self, property_id: int | str | None) -> CleanerContact:
        """Back-compat: resolve purely by Beds24 propertyId."""
        return self.resolve(property_id=property_id)

    def _by_name(self, property_name: str) -> CleanerContact | None:
        """A concrete contact for this property name, or None to fall back.

        Returns None both when the property is unassigned and when its assigned
        cleaner has no email yet — callers treat None as "use the default"."""
        key = self._assignments.get(str(property_name or "").strip().casefold())
        if not key:
            return None
        contact = self._cleaners.get(key)
        if contact is None:
            log.warning(
                "Cleaner map: property %r assigned to unknown cleaner key %r — using default",
                property_name, key,
            )
            return None
        if not contact.email.strip():
            log.warning(
                "Cleaner map: cleaner %r (property %r) has no email yet — using default %r",
                key, property_name, self._default.email,
            )
            return None
        return contact

    # -- loading --------------------------------------------------------------

    @classmethod
    def from_yaml(
        cls,
        path: str | Path | None = None,
        *,
        default_email: str = "",
        default_name: str = "Équipe ménage",
        property_map: PropertyMap | None = None,
    ) -> "CleanerMap":
        p = Path(path) if path else _DEFAULT_PATH
        pmap = property_map if property_map is not None else load_property_map()
        try:
            data = yaml.safe_load(p.read_text()) or {}
        except FileNotFoundError:
            log.warning("Cleaner map %s not found — all properties use the default", p)
            return cls(
                property_map=pmap, default_email=default_email, default_name=default_name
            )

        cleaners: dict[str, CleanerContact] = {}
        for key, entry in (data.get("cleaners") or {}).items():
            entry = entry or {}
            cleaners[str(key).strip()] = CleanerContact(
                name=str(entry.get("name") or "").strip() or default_name,
                email=str(entry.get("email") or "").strip(),
            )
        assignments = {
            str(name): str(key) for name, key in (data.get("assignments") or {}).items()
        }
        return cls(
            assignments=assignments,
            cleaners=cleaners,
            property_map=pmap,
            default_email=default_email,
            default_name=default_name,
        )


@functools.lru_cache(maxsize=1)
def load_cleaner_map(default_email: str = "", default_name: str = "Équipe ménage") -> CleanerMap:
    """Process-wide cached cleaner map loaded from the default YAML path."""
    return CleanerMap.from_yaml(default_email=default_email, default_name=default_name)
