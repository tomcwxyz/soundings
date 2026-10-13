"""Scottish official lookup parser and hierarchy regression tests."""

import pytest
from sqlalchemy import text

from soundings.adapters.gov_scot_geography.loader import (
    ScotGeography2022Loader,
    hierarchy_edges,
    place_rows,
)
from soundings.adapters.gov_scot_geography.parser import parse_scot_dz_lookup
from soundings.db.engine import get_engine

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
    conflicted = SAMPLE + b"S01000001,S02000002,S12000036,Other,Other\n"
    with pytest.raises(ValueError, match="Conflicting"):
        parse_scot_dz_lookup(conflicted)


@pytest.mark.integration
async def test_scottish_loader_writes_idempotent_hierarchy() -> None:
    """A sample import works twice against an isolated PostGIS test database."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO geography.place (id,type,code,name) VALUES "
                "('country:S92000003','country','S92000003','Scotland'),"
                "('ltla24:S12000033','ltla24','S12000033','Aberdeen City'),"
                "('ltla24:S12000036','ltla24','S12000036','City of Edinburgh') "
                "ON CONFLICT (id) DO NOTHING"
            )
        )
    loader = ScotGeography2022Loader(engine, minimum_dz=3, minimum_iz=2)
    first = await loader.load_from_bytes(SAMPLE)
    second = await loader.load_from_bytes(SAMPLE)
    assert first.rows_written == 5
    assert second.rows_written == 5
    async with engine.connect() as conn:
        result = (
            await conn.execute(
                text(
                    "SELECT DISTINCT parent_id FROM geography.current_place_hierarchy "
                    "WHERE child_id = 'dz22:S01000001'"
                )
            )
        ).all()
    assert {r.parent_id for r in result} == {
        "iz22:S02000001", "ltla24:S12000033", "country:S92000003"
    }


@pytest.mark.integration
async def test_scottish_loader_fails_if_council_missing() -> None:
    """No orphan Data Zones or partial inserts if the ONS council seed is incomplete."""
    engine = get_engine()
    bad = SAMPLE.replace(b"S12000033", b"S12009999")
    loader = ScotGeography2022Loader(engine, minimum_dz=3, minimum_iz=2)
    with pytest.raises(RuntimeError, match="not seeded"):
        await loader.load_from_bytes(bad)
