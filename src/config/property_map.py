"""
Property display-name ↔ Beds24 propertyId resolution.

Backed by config/beds24_properties.yaml. Used by the /early-checkin form to
list a property's Beds24 reservations. Names match igloohome_devices.yaml so the
property→device lookup (see device_map.py) still resolves for the same property.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "beds24_properties.yaml"


class PropertyMap:
    """Bidirectional property-name ↔ Beds24 propertyId lookup (name is case-insensitive)."""

    def __init__(self, properties: dict[str, int] | None = None):
        props = properties or {}
        self._names = [str(k).strip() for k in props if str(k).strip()]
        self._id_by_name = {
            str(k).strip().casefold(): int(v) for k, v in props.items() if str(k).strip()
        }
        self._name_by_id = {int(v): str(k).strip() for k, v in props.items() if str(k).strip()}

    @property
    def property_names(self) -> list[str]:
        """Property display names in file order (for form dropdowns)."""
        return list(self._names)

    @property
    def property_ids(self) -> list[int]:
        return list(self._name_by_id.keys())

    def id_for(self, property_name: str) -> int | None:
        return self._id_by_name.get(str(property_name or "").strip().casefold())

    def name_for(self, property_id: int | str) -> str:
        try:
            return self._name_by_id.get(int(property_id), "")
        except (TypeError, ValueError):
            return ""

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> "PropertyMap":
        p = Path(path) if path else _DEFAULT_PATH
        try:
            data = yaml.safe_load(p.read_text()) or {}
        except FileNotFoundError:
            log.warning("Property map %s not found — no name↔propertyId mapping", p)
            return cls()
        return cls(properties=data.get("properties", {}))


@functools.lru_cache(maxsize=1)
def load_property_map() -> PropertyMap:
    """Process-wide cached property map loaded from the default YAML path."""
    return PropertyMap.from_yaml()
