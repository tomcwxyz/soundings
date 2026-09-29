import pytest
from sqlalchemy import select

from soundings.db.engine import get_engine
from soundings.db.models.observation import Observation

pytestmark = pytest.mark.integration


async def test_observation_table_exists_and_maps() -> None:
    """A SELECT against data.observation should succeed (table + model map)."""
    engine = get_engine()
    async with engine.connect() as conn:
        await conn.execute(select(Observation.id).limit(0))


async def test_observation_columns_present() -> None:
    """The ORM model exposes the columns created by migration 0009."""
    cols = {c.name for c in Observation.__table__.columns}
    assert cols == {
        "id",
        "organisation_id",
        "place_id",
        "period_start",
        "period_end",
        "theme",
        "statement",
        "indicator_key",
        "value",
        "unit",
        "evidence_type",
        "methodology_note",
        "confidence",
        "submitted_at",
    }


async def test_observation_pk_is_id() -> None:
    pk_cols = {c.name for c in Observation.__table__.primary_key.columns}
    assert pk_cols == {"id"}


async def test_observation_schema_is_data() -> None:
    assert Observation.__table__.schema == "data"
    assert Observation.__tablename__ == "observation"
