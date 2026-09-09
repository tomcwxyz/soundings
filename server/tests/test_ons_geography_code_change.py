import io
import zipfile
from datetime import date

import pytest
from sqlalchemy import select, text

from soundings.adapters.ons_geography.code_change_loader import (
    OnsGeographyCodeChangeLoader,
    _parse_date,
)
from soundings.db.engine import get_engine
from soundings.db.models.geography import CodeChange

pytestmark = pytest.mark.integration

SAMPLE_CSV = b"""GEOGCD_O,GEOGCD_N,GEOGCHGTYPE,EFFECTIVE_DATE,NOTES
E07000004,E06000060,Replacement,2020-04-01,Buckinghamshire UA created
E07000005,E06000060,Replacement,2020-04-01,Buckinghamshire UA created
E08000020,E08000037,Reorganisation,2018-04-01,Gateshead boundary change
"""


def _history_zip(csv_bytes: bytes, name: str = "CHD/Geography_History.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as zf:
        zf.writestr(name, csv_bytes)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2020-04-01", date(2020, 4, 1)),
        ("01/04/2020", date(2020, 4, 1)),
        ("01-Apr-2020", date(2020, 4, 1)),
        ("20200401", date(2020, 4, 1)),
    ],
)
def test_parse_date_accepts_supported_chd_formats(raw: str, expected: date) -> None:
    assert _parse_date(raw) == expected


def test_parse_date_rejects_invalid_values() -> None:
    assert _parse_date("not-a-date") is None
    assert _parse_date("31/02/2020") is None
    assert _parse_date(" ") is None


async def test_code_change_loader_inserts_rows() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM geography.code_change"))

    loader = OnsGeographyCodeChangeLoader(engine)
    result = await loader.load_from_bytes(SAMPLE_CSV)
    assert result.rows_written == 3

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(
                    CodeChange.old_code,
                    CodeChange.new_code,
                    CodeChange.change_type,
                ).order_by(CodeChange.id)
            )
        ).all()
    assert (rows[0].old_code, rows[0].new_code) == ("E07000004", "E06000060")
    assert rows[2].change_type == "Reorganisation"


async def test_code_change_loader_is_idempotent() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM geography.code_change"))

    loader = OnsGeographyCodeChangeLoader(engine)
    await loader.load_from_bytes(SAMPLE_CSV)
    await loader.load_from_bytes(SAMPLE_CSV)
    async with engine.connect() as conn:
        n = (await conn.execute(text("SELECT count(*) FROM geography.code_change"))).scalar_one()
    assert n == 3


async def test_zip_loader_accepts_geography_history_filename() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM geography.code_change"))

    loader = OnsGeographyCodeChangeLoader(engine)
    result = await loader.load_from_zip_bytes(_history_zip(SAMPLE_CSV))

    assert result.rows_written == 3


async def test_zip_loader_fails_when_history_rows_are_unparseable() -> None:
    engine = get_engine()
    malformed = b"""GEOGCD_O,GEOGCD_N,GEOGCHGTYPE,EFFECTIVE_DATE
E07000004,E06000060,Replacement,not-a-date
"""
    loader = OnsGeographyCodeChangeLoader(engine)

    with pytest.raises(ValueError, match="no parseable Geography History rows"):
        await loader.load_from_zip_bytes(_history_zip(malformed))
