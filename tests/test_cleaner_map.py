"""
Cleaner map: property (by name or Beds24 id) → cleaner contact, default fallback.

Property name ↔ id reuses the same PropertyMap the /early-checkin form uses.
"""

from src.config.cleaner_map import CleanerContact, CleanerMap
from src.config.property_map import PropertyMap

# Le Fernand = 328510, Le Matisse = 326234 (from config/beds24_properties.yaml).
_PMAP = PropertyMap({"Le Fernand": 328510, "Le Matisse": 326234, "Terracotta": 326123})

_YAML = """
cleaners:
  v_clean:
    name: "V-Clean"
    email: "vclean@example.com"
  guilherme:
    name: "Guilherme Veloso"
    email: ""          # TODO placeholder — should fall back to default
assignments:
  Le Fernand: v_clean
  Le Matisse: guilherme
  Terracotta: nobody   # points at a cleaner key that doesn't exist → default
"""


def _write(tmp_path, text):
    p = tmp_path / "cleaners.yaml"
    p.write_text(text)
    return p


def _map(tmp_path):
    return CleanerMap.from_yaml(
        _write(tmp_path, _YAML), default_email="fallback@x.com", property_map=_PMAP
    )


def test_resolve_by_property_name(tmp_path):
    m = _map(tmp_path)
    assert m.resolve(property_name="Le Fernand") == CleanerContact(
        name="V-Clean", email="vclean@example.com"
    )


def test_resolve_by_property_name_is_case_insensitive(tmp_path):
    m = _map(tmp_path)
    assert m.resolve(property_name="le fernand").email == "vclean@example.com"


def test_resolve_by_property_id_when_name_absent(tmp_path):
    m = _map(tmp_path)
    # No name on the payload → fall back to the enriched propertyId (328510).
    assert m.resolve(property_id=328510).email == "vclean@example.com"
    assert m.resolve(property_id="328510").email == "vclean@example.com"  # numeric string


def test_name_takes_precedence_but_id_is_the_fallback(tmp_path):
    m = _map(tmp_path)
    # Unknown name but a known id → still routes via the id.
    assert m.resolve(property_name="Chez Untel", property_id=328510).email == "vclean@example.com"


def test_assigned_cleaner_with_blank_email_falls_back(tmp_path):
    m = _map(tmp_path)
    assert m.resolve(property_name="Le Matisse").email == "fallback@x.com"


def test_assignment_to_unknown_cleaner_key_falls_back(tmp_path):
    m = _map(tmp_path)
    assert m.resolve(property_name="Terracotta").email == "fallback@x.com"


def test_unmapped_property_and_missing_ids_fall_back(tmp_path):
    m = _map(tmp_path)
    assert m.resolve(property_name="Nowhere").email == "fallback@x.com"
    assert m.resolve(property_id=999999).email == "fallback@x.com"
    assert m.resolve().email == "fallback@x.com"


def test_missing_file_uses_default():
    m = CleanerMap.from_yaml(
        "/nonexistent/cleaners.yaml", default_email="fallback@x.com", property_map=_PMAP
    )
    assert m.resolve(property_name="Le Fernand").email == "fallback@x.com"


def test_seeded_repo_config_loads():
    """The committed config/cleaners.yaml parses and routes against the real
    beds24_properties.yaml — both the HostBuddy title and the Beds24 name of
    Le Fernand map to V-Clean; Le Matisse maps to Virginie."""
    m = CleanerMap.from_yaml(default_email="fallback@x.com")
    assert m.resolve(property_name="Le Fernand | Campus Parking").email == "vcleangrenoble@gmail.com"
    assert m.resolve(property_name="Le Fernand").email == "vcleangrenoble@gmail.com"
    assert m.resolve(property_name="Le Matisse").email == "labelportos@hotmail.fr"
    assert m.resolve(property_name="Velours T2").email == "vcleangrenoble@gmail.com"
    assert m.resolve(property_name="Studio Écrin").email == "vcleangrenoble@gmail.com"
    # A genuinely unknown property notifies the owner, not a wrong cleaner.
    assert m.resolve(property_name="Chez Personne").email == "fallback@x.com"
