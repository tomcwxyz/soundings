"""WIMD ingestion integration tests; isolated test Postgres is required."""

import pytest
from sqlalchemy import text

from soundings.adapters.gov_wales_wimd2025.loader import Wimd2025Loader
from soundings.db.engine import get_engine

pytestmark = pytest.mark.integration
SAMPLE = (
    b"Data values,Data description,Area code,Area name,Domain\n"
    b"570,Rank,W01000003,Amlwch Port,WIMD\n"
    b"3,Decile,W01000003,Amlwch Port,WIMD\n"
    b"407,Rank,W01000003,Amlwch Port,Income\n"
    b"90,Rank,W01000004,Neighbourhood,WIMD\n"
    b"1,Decile,W01000004,Neighbourhood,WIMD\n"
)


async def test_wimd_loads_and_is_idempotent_on_welsh_places() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO geography.place (id,type,code,name) VALUES "
                "('lsoa21:W01000003','lsoa21','W01000003','Amlwch Port'), "
                "('lsoa21:W01000004','lsoa21','W01000004','Neighbourhood') "
                "ON CONFLICT (id) DO NOTHING"
            )
        )
    loader = Wimd2025Loader(engine, minimum_lsoas=2)
    first = await loader.load_from_bytes(SAMPLE)
    again = await loader.load_from_bytes(SAMPLE)
    assert first.rows_written == 5
    assert again.rows_written == 5
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT indicator_key, value FROM data.indicator_value "
                "WHERE place_id = 'lsoa21:W01000003' "
                "AND source_id = 'gov_wales.wimd2025'"
            )
        )
        values = {r.indicator_key: float(r.value) for r in result}
    assert values["deprivation.wimd2025.rank"] == 570
    assert values["deprivation.wimd2025.decile"] == 3


async def test_wimd_rejects_incomplete_extract_before_database_writes() -> None:
    loader = Wimd2025Loader(get_engine())
    with pytest.raises(ValueError, match="Incomplete"):
        await loader.load_from_bytes(SAMPLE)
