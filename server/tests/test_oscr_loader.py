"""Isolated-DB OSCR import: idempotence, source safety, registered vs operating."""

import io
import zipfile
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text

from soundings.adapters.oscr.loader import (
    OscrLoader,
    build_organisation_rows,
    normalise_area,
)
from soundings.adapters.oscr.parser import parse_register_csv
from soundings.db.engine import get_engine

CSV = (
    b"Charity Number,Charity Name,Charity Status,Postcode,Registration Date,"
    b"Main Operating Location,Geographical Spread,Income,Expenditure,Purposes\n"
    b"SC000001,Local Example,Active,EH47 8BY,26/06/1986,West Lothian,"
    b"Local,1000,50,Community\n"
    b"SC000002,Other Group,Not Monitored,EH1 2XY,03/04/2004,City of Edinburgh,"
    b"Scotland and UK,,100,Education\n"
)


def zip_csv(data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("ScottishRegister.csv", data)
    return buf.getvalue()


def test_registered_address_not_assumed_to_be_operating_area() -> None:
    records = parse_register_csv(CSV)
    orgs = build_organisation_rows(
        records,
        {"EH478BY": "ltla24:S12000040"},
        {"edinburgh": "ltla24:S12000036"},
        datetime.now(tz=UTC),
    )
    assert orgs[0]["registered_address_place_id"] == "ltla24:S12000040"
    assert orgs[0]["raw"]["main_operating_place_id"] is None
    assert orgs[1]["registered_address_place_id"] is None
    assert orgs[1]["raw"]["main_operating_place_id"] == "ltla24:S12000036"
    assert normalise_area("City of Edinburgh") == "edinburgh"


@pytest_asyncio.fixture
async def clean_oscr_rows():
    """Restore shared test DB even if a source importer assertion fails."""
    engine = get_engine()

    async def clean() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM data.indicator_value WHERE source_id = 'oscr'"))
            await conn.execute(text("DELETE FROM data.organisation WHERE source_id = 'oscr'"))
            await conn.execute(
                text("DELETE FROM data.organisation WHERE id = 'charity_commission:999999'")
            )
            await conn.execute(text("DELETE FROM geography.postcode WHERE postcode = 'EH478BY'"))
            await conn.execute(text("DELETE FROM geography.place WHERE id = 'ltla24:S12000040'"))

    await clean()
    try:
        yield
    finally:
        await clean()


@pytest.mark.integration
async def test_oscr_import_is_idempotent_and_does_not_touch_ccew(clean_oscr_rows) -> None:
    engine = get_engine()
    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO geography.place (id,type,code,name) VALUES "
                "('ltla24:S12000040','ltla24','S12000040','West Lothian') "
                "ON CONFLICT (id) DO NOTHING"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO geography.postcode "
                "(postcode,ltla24,latitude,longitude,retrieved_at) "
                "VALUES ('EH478BY','ltla24:S12000040',55.8,-3.5,:now) "
                "ON CONFLICT (postcode) DO UPDATE SET ltla24 = EXCLUDED.ltla24"
            ),
            {"now": now},
        )
        await conn.execute(
            text(
                "INSERT INTO data.organisation "
                "(id,name,classification,source_id,retrieved_at,raw) "
                "VALUES ('charity_commission:999999','Independent CCEW',"
                "ARRAY[]::varchar[],'charity_commission',:now,'{}'::jsonb) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"now": now},
        )
    loader = OscrLoader(engine, minimum_charities=2)
    first = await loader.load_from_bytes(zip_csv(CSV))
    second = await loader.load_from_bytes(zip_csv(CSV))
    assert first.rows_written == second.rows_written == 2
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id,source_id,registered_address_place_id,raw "
                    "FROM data.organisation WHERE id IN "
                    "('oscr:SC000001','oscr:SC000002','charity_commission:999999')"
                )
            )
        ).all()
    assert len(rows) == 3
    west = next(row for row in rows if row.id == "oscr:SC000001")
    assert west.registered_address_place_id == "ltla24:S12000040"
    assert west.raw["in_current_extract"] is True
    cc = next(row for row in rows if row.id == "charity_commission:999999")
    assert cc.source_id == "charity_commission"


@pytest.mark.integration
async def test_incomplete_oscr_snapshot_does_not_touch_database() -> None:
    loader = OscrLoader(get_engine(), minimum_charities=10)
    with pytest.raises(ValueError, match="Incomplete"):
        await loader.load_from_bytes(zip_csv(CSV))
