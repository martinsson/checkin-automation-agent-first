"""
Cleaner map: propertyId → cleaner email resolution with default fallback.
"""

from src.config.cleaner_map import CleanerContact, CleanerMap

_YAML = """
properties:
  326123:
    name: "V-Clean"
    email: "vclean@example.com"
  326234:
    name: "Guilherme Veloso"
    email: ""          # TODO placeholder — should fall back to default
"""


def _write(tmp_path, text):
    p = tmp_path / "cleaners.yaml"
    p.write_text(text)
    return p


def test_mapped_property_with_email(tmp_path):
    m = CleanerMap.from_yaml(_write(tmp_path, _YAML), default_email="fallback@x.com")
    assert m.for_property(326123) == CleanerContact(name="V-Clean", email="vclean@example.com")


def test_mapped_property_blank_email_falls_back(tmp_path):
    m = CleanerMap.from_yaml(_write(tmp_path, _YAML), default_email="fallback@x.com")
    contact = m.for_property(326234)
    assert contact.email == "fallback@x.com"


def test_unmapped_property_falls_back(tmp_path):
    m = CleanerMap.from_yaml(_write(tmp_path, _YAML), default_email="fallback@x.com")
    assert m.for_property(999999).email == "fallback@x.com"


def test_string_and_none_property_ids(tmp_path):
    m = CleanerMap.from_yaml(_write(tmp_path, _YAML), default_email="fallback@x.com")
    assert m.for_property("326123").email == "vclean@example.com"   # numeric string
    assert m.for_property(None).email == "fallback@x.com"           # missing id


def test_missing_file_uses_default():
    m = CleanerMap.from_yaml("/nonexistent/cleaners.yaml", default_email="fallback@x.com")
    assert m.for_property(326123).email == "fallback@x.com"


def test_seeded_repo_config_loads():
    """The committed config/cleaners.yaml parses and routes (emails are TODO
    placeholders, so every property currently falls back to the default)."""
    m = CleanerMap.from_yaml(default_email="fallback@x.com")
    # Terracotta is seeded but its email is a blank TODO → default for now.
    assert m.for_property(326123).email == "fallback@x.com"
