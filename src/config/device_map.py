"""
Property → Igloohome device-id resolution.

Backed by config/igloohome_devices.yaml (device ids sourced from Beds24
property template variable 8). Used to fill `deviceId` in the Make door-code
webhook payload.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "igloohome_devices.yaml"


class DeviceMap:
    """Case-insensitive property-name → device-id lookup with a default fallback."""

    def __init__(self, default: str = "", properties: dict[str, str] | None = None):
        self._default = default or ""
        props = properties or {}
        # preserve original display names (file order + casing) for UI listing
        self._names = [str(k).strip() for k in props if str(k).strip()]
        # normalise keys to casefolded for case-insensitive matching
        self._by_name = {
            str(k).strip().casefold(): str(v or "")
            for k, v in props.items()
        }

    @property
    def property_names(self) -> list[str]:
        """Property display names in file order (for form dropdowns, etc.)."""
        return list(self._names)

    def device_for(self, property_name: str) -> str:
        """Return the device id for a property, or the default if unknown/empty."""
        key = str(property_name or "").strip().casefold()
        if key and key in self._by_name and self._by_name[key]:
            return self._by_name[key]
        return self._default

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> "DeviceMap":
        p = Path(path) if path else _DEFAULT_PATH
        try:
            data = yaml.safe_load(p.read_text()) or {}
        except FileNotFoundError:
            log.warning("Device map %s not found — no property→device mapping", p)
            return cls()
        return cls(default=data.get("default", ""), properties=data.get("properties", {}))


@functools.lru_cache(maxsize=1)
def load_device_map() -> DeviceMap:
    """Process-wide cached device map loaded from the default YAML path."""
    return DeviceMap.from_yaml()
