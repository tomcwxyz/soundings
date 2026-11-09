from soundings.adapters.ons_geography.chains import (
    ALL_CHAINS,
    LSOA_MSOA_LTLA,
    LTLA_UTLA_REGION_COUNTRY,
    WARD_LTLA_UTLA,
)


def test_all_canonical_levels_appear_as_a_child_or_parent_in_some_chain() -> None:
    types_seen: set[str] = set()
    for chain in ALL_CHAINS:
        for place_type, _ in chain.levels:
            types_seen.add(place_type)
    # Note: msoa21 is no longer in the LSOA→LTLA chain (ONS simplified the lookup)
    # It still exists as a boundary layer but isn't part of any hierarchy chain
    expected = {
        "lsoa21",
        "ltla24",
        "utla24",
        "region",
        "country",
        "ward24",
    }
    missing = expected - types_seen
    assert not missing, f"chains do not cover {missing}"


def test_lsoa_chain_descends_to_lad() -> None:
    types = [t for t, _ in LSOA_MSOA_LTLA.levels]
    # ONS dropped the MSOA intermediate layer in the LSOA21_WD24_LAD24_EW_LU lookup
    assert types == ["lsoa21", "ltla24"]


def test_admin_chain_climbs_to_country() -> None:
    types = [t for t, _ in LTLA_UTLA_REGION_COUNTRY.levels]
    assert types == ["ltla24", "utla24", "region", "country"]


def test_ward_chain_contains_only_administrative_parents() -> None:
    assert WARD_LTLA_UTLA.levels == [
        ("ward24", "WD24CD"),
        ("ltla24", "LAD24CD"),
        ("utla24", "UTLA24CD"),
    ]
