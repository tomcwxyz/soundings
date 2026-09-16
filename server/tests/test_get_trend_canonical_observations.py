from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text

from soundings.adapters.base import LoaderAdapter, LoaderResult
from soundings.db.engine import get_engine
from soundings.orchestration.orchestrator import IndicatorOrchestrator
from soundings.orchestration.registry import AdapterRegistry
from soundings.tools.get_trend import GetTrendInput, get_trend

pytestmark = pytest.mark.integration

SOURCE_ID = "test.temporal.loader"
INDICATOR = "test.temporal.metric"
PLACE_ID = "ltla24:E09999999"


class _LoaderStub(LoaderAdapter):
    source_id = SOURCE_ID

    async def load(self, run_id: str | None = None) -> LoaderResult:
        return LoaderResult(rows_written=0)


@pytest_asyncio.fixture(autouse=True)
async def _cleanup() -> AsyncIterator[None]:
    yield
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM data.trend_point WHERE indicator_key = :ik"),
            {"ik": INDICATOR},
        )
        await conn.execute(
            text("DELETE FROM data.indicator_value WHERE indicator_key = :ik"),
            {"ik": INDICATOR},
        )
        await conn.execute(text("DELETE FROM geography.place WHERE id = :pid"), {"pid": PLACE_ID})
        await conn.execute(
            text("DELETE FROM catalogue.indicator WHERE key = :ik"),
            {"ik": INDICATOR},
        )
        await conn.execute(
            text("DELETE FROM catalogue.source WHERE id = :sid"),
            {"sid": SOURCE_ID},
        )


async def _seed() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO catalogue.source "
                "(id, label, publisher, licence, mode, rate_limit) "
                "VALUES (:sid, 'Temporal test', 'Test', 'CC0', 'loader', '{}'::jsonb)"
            ),
            {"sid": SOURCE_ID},
        )
        await conn.execute(
            text(
                "INSERT INTO catalogue.indicator "
                "(key, label, unit, source_id, available_at, caveats, related_keys) "
                "VALUES (:ik, 'Temporal metric', 'count', :sid, ARRAY['ltla24'], "
                "'[]'::jsonb, ARRAY[]::varchar[])"
            ),
            {"ik": INDICATOR, "sid": SOURCE_ID},
        )
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) "
                "VALUES (:pid, 'ltla24', 'E09999999', 'Temporal Test Place')"
            ),
            {"pid": PLACE_ID},
        )
        for period, value in [
            ("2024-Q4", 14.0),
            ("2023-Q4", 10.0),
            ("2024-Q1", 11.0),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO data.indicator_value "
                    "(place_id, indicator_key, period, value, source_id, retrieved_at, caveats) "
                    "VALUES (:pid, :ik, :period, :value, :sid, NOW(), '[]'::jsonb)"
                ),
                {
                    "pid": PLACE_ID,
                    "ik": INDICATOR,
                    "period": period,
                    "value": value,
                    "sid": SOURCE_ID,
                },
            )


async def test_loader_trend_falls_back_to_indicator_observations() -> None:
    await _seed()
    registry = AdapterRegistry(get_engine())
    registry.register(SOURCE_ID, lambda engine: _LoaderStub(engine))
    orchestrator = IndicatorOrchestrator(get_engine(), registry)

    result = await get_trend(
        GetTrendInput(
            place_id=PLACE_ID,
            indicator=INDICATOR,
            period_from="2024-Q1",
            period_to="2024-Q4",
        ),
        orchestrator,
    )

    assert result.trend is not None
    assert [point.period for point in result.trend.points] == ["2024-Q1", "2024-Q4"]
    assert [point.value for point in result.trend.points] == [11.0, 14.0]
    assert result.trend.points[0].temporal is not None
    assert result.trend.points[0].temporal.granularity == "quarter"
