"""aggregate_sub_areas: counts and averages over every neighbourhood.

get_sub_areas returns a ranked slice (50 by default), and the baseline saw
the model count deciles or average values off that slice as if it were the
whole place. This tool works over all of a parent's sub-areas and says how
many had a value.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from soundings.db.engine import get_engine
from soundings.tools.aggregate_sub_areas import AggregateSubAreasInput, aggregate_sub_areas

pytestmark = pytest.mark.integration

PARENT = "ltla24:AGGTEST1"
# (decile, population): 60 neighbourhoods so a 50-row slice would undercount.
ROWS = [(1, 1500)] * 12 + [(2, 1600)] * 18 + [(6, 1700)] * 30
DECILE = "deprivation.imd.decile"


async def _seed() -> None:
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
                "INSERT INTO geography.place (id, type, code, name) "
                "VALUES (:id, 'ltla24', 'E09AGG001', 'Aggregate LTLA')"
            ),
            {"id": PARENT},
        )
        for i, (decile, population) in enumerate([*ROWS, (None, 1400)]):
            cid = f"lsoa21:E01AGG{i:03d}"
            await conn.execute(
                text(
                    "INSERT INTO geography.place (id, type, code, name) "
                    "VALUES (:id, 'lsoa21', :code, :name)"
                ),
                {"id": cid, "code": cid.split(":")[1], "name": f"Agg {i}"},
            )
            await conn.execute(
                text("INSERT INTO geography.place_hierarchy (parent_id, child_id) VALUES (:p, :c)"),
                {"p": PARENT, "c": cid},
            )
            rows = [("population.total", population, "ons.mid_year_estimates")]
            if decile is not None:
                rows.append((DECILE, decile, "mhclg.imd2025"))
            for key, value, source in rows:
                await conn.execute(
                    text(
                        "INSERT INTO data.indicator_value "
                        "(place_id, indicator_key, period, value, source_id, retrieved_at, "
                        "caveats) VALUES (:pid, :k, '2025', :v, :src, :ret, '[]'::jsonb)"
                    ),
                    {"pid": cid, "k": key, "v": value, "src": source, "ret": now},
                )


def _orchestrator() -> MagicMock:
    orchestrator = MagicMock()
    orchestrator._fetch_one = AsyncMock(return_value=None)
    return orchestrator


async def test_counts_every_neighbourhood_not_a_slice() -> None:
    await _seed()

    out = await aggregate_sub_areas(
        AggregateSubAreasInput(
            place_id=PARENT, indicator_key=DECILE, statistic="count_at_or_below", threshold=1
        ),
        _orchestrator(),
        get_engine(),
    )

    assert out.result == 12
    assert out.n_with_values == 60
    assert out.total_sub_areas == 61
    assert out.share_of_with_values == pytest.approx(0.2)
    assert [s.source_id for s in out.sources] == ["mhclg.imd2025"]
    assert any("1 of 61" in c for c in out.caveats)


async def test_population_weighted_mean_uses_each_neighbourhoods_population() -> None:
    await _seed()

    out = await aggregate_sub_areas(
        AggregateSubAreasInput(
            place_id=PARENT, indicator_key=DECILE, statistic="population_weighted_mean"
        ),
        _orchestrator(),
        get_engine(),
    )

    weights = [p for _, p in ROWS]
    expected = sum(d * p for d, p in ROWS) / sum(weights)
    assert out.result == pytest.approx(expected)
    assert {s.source_id for s in out.sources} == {"mhclg.imd2025", "ons.mid_year_estimates"}


async def test_threshold_is_required_for_counts() -> None:
    with pytest.raises(ValueError, match="threshold"):
        AggregateSubAreasInput(place_id=PARENT, indicator_key=DECILE, statistic="count_at_or_above")
