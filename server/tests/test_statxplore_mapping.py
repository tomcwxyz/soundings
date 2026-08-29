from soundings.adapters.dwp_statxplore.mapping import (
    DEFAULT_MAPPING_PATH,
    load_statxplore_mapping,
)


def test_default_mapping_file_exists() -> None:
    assert DEFAULT_MAPPING_PATH.exists()


def test_mapping_loads_at_least_one_entry() -> None:
    mappings = load_statxplore_mapping()
    assert len(mappings) >= 1


def test_uc_caseload_entry_carries_long_identifiers() -> None:
    mappings = load_statxplore_mapping()
    by_key = {m.indicator_key: m for m in mappings}
    uc = by_key["economy.universal_credit_claimants"]
    assert uc.database.startswith("str:database:")
    assert uc.measures[0].startswith("str:count:")
    assert uc.geography_dim.startswith("str:field:")
    assert uc.date_dim.startswith("str:field:")
    assert "{place_code}" in uc.geography_value_template
    assert uc.place_type == "ltla24"
    assert uc.unit == "people"


def test_mapping_only_exposes_verified_statxplore_indicators() -> None:
    """Speculative rate-shaped mappings must not be advertised as working."""
    mappings = load_statxplore_mapping()
    keys = {m.indicator_key for m in mappings}
    assert keys == {"economy.universal_credit_claimants"}
