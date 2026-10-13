"""Scottish official lookup parser and hierarchy regression tests."""

import pytest

from soundings.adapters.gov_scot_geography.loader import (
    hierarchy_edges,
    place_rows,
)
from soundings.adapters.gov_scot_geography.parser import parse_scot_dz_lookup

SAMPLE = (
    b"DZ22,IZ22,LA,DZ22Name,IZ22Name\n"
    b"S01000001,S02000001,S12000033,Aberdeen zone 1,Aberdeen intermediate\n"
    b"S01000002,S02000001,S12000033,Aberdeen zone 2,Aberdeen intermediate\n"
    b"S01000003,S02000002,S12000036,Edinburgh zone 1,Edinburgh intermediate\n"
)


def test_parse_and_transitive_parent_chains() -> None:
    parsed = parse_scot_dz_lookup(SAMPLE)
    assert len(parsed) == 3
    assert len(place_rows(parsed)) == 5
    edges = {(e["child_id"], e["parent_id"]) for e in hierarchy_edges(parsed)}
    assert ("dz22:S01000001", "iz22:S02000001") in edges
    assert ("dz22:S01000001", "ltla24:S12000033") in edges
    assert ("iz22:S02000002", "ltla24:S12000036") in edges
    assert ("dz22:S01000003", "country:S92000003") in edges


def test_names_optional_and_bom_supported() -> None:
    sample = "\ufeffDZ22,IZ22,LA\nS01000001,S02000001,S12000033\n"
    rows = parse_scot_dz_lookup(sample.encode())
    assert rows[0].dz_name == "S01000001"


def test_missing_required_columns_rejected() -> None:
    with pytest.raises(ValueError, match="missing"):
        parse_scot_dz_lookup(b"DZ22,IZ22\nS01000001,S02000001\n")


def test_wrong_country_codes_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid"):
        parse_scot_dz_lookup(SAMPLE.replace(b"S12000033", b"W06000033"))


def test_conflicting_dz_parent_rejected() -> None:
    conflicted = (
        SAMPLE + b"S01000001,S02000002,S12000036,Other,Other\n"
    )
    with pytest.raises(ValueError, match="Conflicting"):
        parse_scot_dz_lookup(conflicted)
