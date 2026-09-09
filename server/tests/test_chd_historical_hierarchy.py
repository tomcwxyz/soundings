import io
import zipfile
from datetime import date

import pytest
from sqlalchemy import text

from soundings.adapters.ons_geography.chd_hierarchy_loader import (
    OnsGeographyHistoricalHierarchyLoader,
    parse_chd_date,
    relationship_from_chd_row,
)
from soundings.db.engine import get_engine

pytestmark = pytest.mark.integration

_TEST_PLACE_IDS = (
    "lsoa21:L1",
    "msoa21:M1",
    "ltla24:LA1",
    "region:R1",
    "country:C1",
)


def _history_zip(rows: list[str]) -> bytes:
    header = "GEOGCD,GEOGNM,OPER_DATE,TERM_DATE,PARENTCD,ENTITYCD,STATUS\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as zf:
        zf.writestr("ChangeHistory.csv", header + "".join(rows))
    return buffer.getvalue()


async def _seed_places() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM geography.place_hierarchy "
                "WHERE child_id = ANY(:ids) OR parent_id = ANY(:ids)"
            ),
            {"ids": list(_TEST_PLACE_IDS)},
        )
        await conn.execute(
            text("DELETE FROM geography.place WHERE id = ANY(:ids)"),
            {"ids": list(_TEST_PLACE_IDS)},
        )
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) VALUES "
                "('lsoa21:L1', 'lsoa21', 'L1', 'Neighbourhood'), "
                "('msoa21:M1', 'msoa21', 'M1', 'Middle area'), "
                "('ltla24:LA1', 'ltla24', 'LA1', 'District'), "
                "('region:R1', 'region', 'R1', 'Region'), "
                "('country:C1', 'country', 'C1', 'Country')"
            )
        )


def test_parse_live_chd_timestamps() -> None:
    assert parse_chd_date("01/04/2009 00:00") == date(2009, 4, 1)
    assert parse_chd_date("1/4/2026 00:00:00") == date(2026, 4, 1)
    assert parse_chd_date("") is None


def test_relationship_converts_inclusive_term_to_exclusive_end() -> None:
    relationship = relationship_from_chd_row(
        {
            "GEOGCD": "E07000001",
            "PARENTCD": "E10000001",
            "OPER_DATE": "01/01/2009 00:00",
            "TERM_DATE": "31/03/2009 00:00",
        }
    )

    assert relationship == (
        "E07000001",
        "E10000001",
        date(2009, 1, 1),
        date(2009, 4, 1),
    )


async def test_loader_writes_direct_dated_edges_and_skips_unknown_codes() -> None:
    await _seed_places()
    engine = get_engine()
    async with engine.begin() as conn:
        # Prove a dated relationship can coexist with the same undated current snapshot.
        await conn.execute(
            text(
                "INSERT INTO geography.place_hierarchy (child_id, parent_id) "
                "VALUES ('lsoa21:L1', 'msoa21:M1')"
            )
        )

    blob = _history_zip(
        [
            "L1,Neighbourhood,01/01/2020 00:00,,M1,E01,live\n",
            "M1,Middle area,01/01/2010 00:00,,LA1,E02,live\n",
            "LA1,District,01/01/2009 00:00,,R1,E06,live\n",
            "R1,Region,01/01/2009 00:00,,C1,E12,live\n",
            "OLD,Old place,01/01/2009 00:00,31/03/2010 00:00,MISSING,E05,terminated\n",
        ]
    )

    result = await OnsGeographyHistoricalHierarchyLoader(engine).load_from_zip_bytes(blob)

    assert result.rows_written == 4
    assert result.notes is not None
    assert "unresolved_rows=1" in result.notes

    async with engine.connect() as conn:
        pair_rows = (
            await conn.execute(
                text(
                    "SELECT valid_from, valid_to FROM geography.place_hierarchy "
                    "WHERE child_id='lsoa21:L1' AND parent_id='msoa21:M1' "
                    "ORDER BY valid_from NULLS FIRST"
                )
            )
        ).all()
        current_view_count = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM geography.current_place_hierarchy "
                    "WHERE child_id='lsoa21:L1' AND parent_id='msoa21:M1'"
                )
            )
        ).scalar_one()

    assert len(pair_rows) == 2
    assert pair_rows[0].valid_from is None
    assert pair_rows[1].valid_from == date(2020, 1, 1)
    assert current_view_count == 1


async def test_loader_refresh_updates_revised_termination_date() -> None:
    await _seed_places()
    engine = get_engine()
    loader = OnsGeographyHistoricalHierarchyLoader(engine)

    await loader.load_from_zip_bytes(
        _history_zip(["L1,Neighbourhood,01/01/2020 00:00,,M1,E01,live\n"])
    )
    await loader.load_from_zip_bytes(
        _history_zip(["L1,Neighbourhood,01/01/2020 00:00,31/12/2024 00:00,M1,E01,terminated\n"])
    )

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT valid_from, valid_to FROM geography.place_hierarchy "
                    "WHERE child_id='lsoa21:L1' AND parent_id='msoa21:M1' "
                    "AND valid_from IS NOT NULL"
                )
            )
        ).one()

    assert row.valid_from == date(2020, 1, 1)
    assert row.valid_to == date(2025, 1, 1)
