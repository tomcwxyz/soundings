"""Regression coverage for current-only hierarchy consumers."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from soundings.db.engine import get_engine
from soundings.tools.get_sub_areas import GetSubAreasInput, get_sub_areas

pytestmark = pytest.mark.integration


async def _seed_current_and_expired_edges() -> None:
    engine = get_engine()
    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.trend_point"))
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM geography.postcode"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) VALUES "
                "('ltla24:PARENT', 'ltla24', 'PARENT', 'Parent'), "
                "('lsoa21:EXPIRED', 'lsoa21', 'EXPIRED', 'Expired child'), "
                "('lsoa21:DATED', 'lsoa21', 'DATED', 'Dated current child'), "
                "('lsoa21:UNDATED', 'lsoa21', 'UNDATED', 'Undated current child')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO geography.place_hierarchy "
                "(child_id, parent_id, valid_from, valid_to) VALUES "
                "('lsoa21:EXPIRED', 'ltla24:PARENT', '2010-01-01', '2020-01-01'), "
                "('lsoa21:DATED', 'ltla24:PARENT', '2020-01-01', NULL), "
                "('lsoa21:UNDATED', 'ltla24:PARENT', NULL, NULL)"
            )
        )
        for place_id, value in (
            ("lsoa21:EXPIRED", 99.0),
            ("lsoa21:DATED", 20.0),
            ("lsoa21:UNDATED", 10.0),
        ):
            await conn.execute(
                text(
                    "INSERT INTO data.indicator_value "
                    "(place_id, indicator_key, period, value, source_id, retrieved_at, caveats) "
                    "VALUES (:pid, 'population.total', '2024', :value, "
                    "'ons.mid_year_estimates', :retrieved_at, '[]'::jsonb)"
                ),
                {"pid": place_id, "value": value, "retrieved_at": now},
            )


async def test_current_hierarchy_view_excludes_expired_edges() -> None:
    await _seed_current_and_expired_edges()
    engine = get_engine()

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT child_id FROM geography.current_place_hierarchy "
                    "WHERE parent_id = 'ltla24:PARENT' ORDER BY child_id"
                )
            )
        ).all()

    assert [row.child_id for row in rows] == ["lsoa21:DATED", "lsoa21:UNDATED"]


async def test_get_sub_areas_does_not_leak_expired_children() -> None:
    await _seed_current_and_expired_edges()
    engine = get_engine()
    orchestrator = MagicMock()
    orchestrator._fetch_one = AsyncMock(return_value=None)

    result = await get_sub_areas(
        GetSubAreasInput(
            place_id="ltla24:PARENT",
            indicator_key="population.total",
        ),
        orchestrator,
        engine,
    )

    ids = {area.place_id for area in result.sub_areas}
    assert ids == {"lsoa21:DATED", "lsoa21:UNDATED"}
    assert "lsoa21:EXPIRED" not in ids
