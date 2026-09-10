import io
import zipfile
from datetime import date

import pytest
from sqlalchemy import text

from soundings.adapters.ons_geography.chd_hierarchy_loader import (
    OnsGeographyHistoricalHierarchyLoader,
)
from soundings.adapters.ons_geography.chd_places_loader import (
    OnsGeographyHistoricalPlacesLoader,
    historical_place_id,
    historical_place_type,
)
from soundings.db.engine import get_engine

pytestmark = pytest.mark.integration

_TEST_IDS = (
    "gss_e07:OLD000001",
    "gss_e07:BAD000001",
    "gss_e07:LIVEONLY1",
    "ltla24:LIVE00001",
    "region:REG000001",
)


def _history_zip(rows: list[str]) -> bytes:
    header = "GEOGCD,GEOGNM,OPER_DATE,TERM_DATE,PARENTCD,ENTITYCD,STATUS\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as zf:
        zf.writestr("ChangeHistory.csv", header + "".join(rows))
    return buffer.getvalue()


async def _reset_test_places() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM geography.place_hierarchy "
                "WHERE child_id = ANY(:ids) OR parent_id = ANY(:ids)"
            ),
            {"ids": list(_TEST_IDS)},
        )
        await conn.execute(
            text("DELETE FROM geography.place WHERE id = ANY(:ids)"),
            {"ids": list(_TEST_IDS)},
        )


def test_historical_identity_preserves_gss_entity_code() -> None:
    assert historical_place_type("E07") == "gss_e07"
    assert historical_place_id("E07", "E07000001") == "gss_e07:E07000001"


async def test_loader_creates_extinct_places_without_shadowing_current_codes() -> None:
    await _reset_test_places()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) VALUES "
                "('ltla24:LIVE00001', 'ltla24', 'LIVE00001', 'Live district')"
            )
        )

    blob = _history_zip(
        [
            "OLD000001,Old district,01/01/2009 00:00,31/03/2023 00:00,,E07,terminated\n",
            "OLD000001,Old district renamed,01/04/2012 00:00,31/03/2023 00:00,,E07,terminated\n",
            "LIVE00001,Live district,01/01/2009 00:00,,,E07,live\n",
            "LIVEONLY1,Unsupported live district,01/01/2009 00:00,,,E07,live\n",
            "BAD000001,Bad date district,01/01/2009 00:00,not-a-date,,E07,terminated\n",
        ]
    )

    loader = OnsGeographyHistoricalPlacesLoader(engine)
    result = await loader.load_from_zip_bytes(blob)
    await loader.load_from_zip_bytes(blob)

    assert result.rows_written == 1
    assert result.notes is not None
    assert "existing_codes=1" in result.notes
    assert "live_codes=1" in result.notes
    assert "invalid_rows=1" in result.notes

    async with engine.connect() as conn:
        historical = (
            await conn.execute(
                text(
                    "SELECT id, type, code, name, valid_from, valid_to "
                    "FROM geography.place WHERE id='gss_e07:OLD000001'"
                )
            )
        ).one()
        historical_count = (
            await conn.execute(text("SELECT count(*) FROM geography.place WHERE code='OLD000001'"))
        ).scalar_one()
        shadow_count = (
            await conn.execute(text("SELECT count(*) FROM geography.place WHERE code='LIVE00001'"))
        ).scalar_one()
        live_only_count = (
            await conn.execute(text("SELECT count(*) FROM geography.place WHERE code='LIVEONLY1'"))
        ).scalar_one()
        bad_count = (
            await conn.execute(text("SELECT count(*) FROM geography.place WHERE code='BAD000001'"))
        ).scalar_one()

    assert historical.type == "gss_e07"
    assert historical.code == "OLD000001"
    assert historical.name == "Old district renamed"
    assert historical.valid_from == date(2009, 1, 1)
    assert historical.valid_to == date(2023, 4, 1)
    assert historical_count == 1
    assert shadow_count == 1
    assert live_only_count == 0
    assert bad_count == 0


async def test_hierarchy_loader_can_resolve_new_extinct_place() -> None:
    await _reset_test_places()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) VALUES "
                "('region:REG000001', 'region', 'REG000001', 'Historic region')"
            )
        )

    blob = _history_zip(
        [
            "OLD000001,Old district,01/01/2009 00:00,31/03/2023 00:00,REG000001,E07,terminated\n",
        ]
    )

    await OnsGeographyHistoricalPlacesLoader(engine).load_from_zip_bytes(blob)
    hierarchy_result = await OnsGeographyHistoricalHierarchyLoader(engine).load_from_zip_bytes(blob)

    assert hierarchy_result.rows_written == 1
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT child_id, parent_id, valid_from, valid_to "
                    "FROM geography.place_hierarchy "
                    "WHERE child_id='gss_e07:OLD000001' AND parent_id='region:REG000001'"
                )
            )
        ).one()

    assert row.valid_from == date(2009, 1, 1)
    assert row.valid_to == date(2023, 4, 1)
