"""Tests for the get_sub_areas tool."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from soundings.db.engine import get_engine
from soundings.tools.get_sub_areas import (
    GetSubAreasInput,
    GetSubAreasOutput,
    SubAreaValue,
    get_sub_areas,
    resolve_sub_area_sort,
    tool_spec,
)


def test_tool_spec_has_correct_name() -> None:
    spec = tool_spec()
    assert spec["name"] == "get_sub_areas"


def test_input_model_requires_place_id_and_indicator() -> None:
    model = GetSubAreasInput(place_id="ltla24:E06000004", indicator_key="deprivation.imd.score")
    assert model.place_id == "ltla24:E06000004"
    assert model.indicator_key == "deprivation.imd.score"
    assert model.child_type == "lsoa21"  # default


def test_output_model_has_sub_areas_list() -> None:
    out = GetSubAreasOutput(
        parent_place_id="ltla24:E06000004",
        indicator_key="deprivation.imd.score",
        child_type="lsoa21",
        sub_areas=[
            SubAreaValue(
                place_id="lsoa21:E01001234",
                name="Stockton 001A",
                value=32.5,
                percentile=85.0,
            ),
        ],
        parent_value=22.0,
        parent_percentile=55.0,
        period="2025",
    )
    assert len(out.sub_areas) == 1
    assert out.sub_areas[0].value == 32.5


@pytest.mark.integration
async def test_sub_areas_with_null_period_does_not_raise() -> None:
    """Regression: calling get_sub_areas without a period (the common case —
    'most deprived neighbourhoods in X') must not trip asyncpg's
    AmbiguousParameterError from the ':period IS NULL' bind. Seeds a parent
    LTLA with LSOA children + values and asserts the query returns them."""
    engine = get_engine()
    parent = "ltla24:SUBTEST"
    children = [
        ("lsoa21:SUB01", "Neigh A", 10.0),
        ("lsoa21:SUB02", "Neigh B", 30.0),
        ("lsoa21:SUB03", "Neigh C", 20.0),
    ]
    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.trend_point"))
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM geography.postcode"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) "
                "VALUES (:id, 'ltla24', 'E09999999', 'Subtest LTLA')"
            ),
            {"id": parent},
        )
        for cid, name, _val in children:
            await conn.execute(
                text(
                    "INSERT INTO geography.place (id, type, code, name) "
                    "VALUES (:id, 'lsoa21', :code, :name)"
                ),
                {"id": cid, "code": cid.split(":")[1], "name": name},
            )
            await conn.execute(
                text("INSERT INTO geography.place_hierarchy (parent_id, child_id) VALUES (:p, :c)"),
                {"p": parent, "c": cid},
            )
        for cid, _name, val in children:
            await conn.execute(
                text(
                    "INSERT INTO data.indicator_value "
                    "(place_id, indicator_key, period, value, source_id, retrieved_at, caveats) "
                    "VALUES (:pid, 'population.total', '2024', :v, :src, :ret, '[]'::jsonb)"
                ),
                {"pid": cid, "v": val, "src": "ons.mid_year_estimates", "ret": now},
            )

    orchestrator = MagicMock()
    orchestrator._fetch_one = AsyncMock(return_value=None)

    out = await get_sub_areas(
        GetSubAreasInput(place_id=parent, indicator_key="population.total", period=None),
        orchestrator,
        engine,
    )

    assert len(out.sub_areas) == 3
    assert {round(s.value or 0) for s in out.sub_areas} == {10, 20, 30}


def test_worst_first_sort_respects_indicator_direction() -> None:
    assert resolve_sub_area_sort("worst_first", "better") == "value_asc"
    assert resolve_sub_area_sort("worst_first", "worse") == "value_desc"
    assert resolve_sub_area_sort("worst_first", None) == "value_desc"
    assert resolve_sub_area_sort("value_desc", "better") == "value_desc"
