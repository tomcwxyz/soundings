"""Integration tests for the detect_insights tool."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from soundings.db.engine import get_engine
from soundings.tools.detect_insights import DetectInsightsInput, detect_insights

pytestmark = pytest.mark.integration

# 10 LTLAs with population.total values from 10k to 500k.
# Index 4 (180k) is the median-ish place that should produce no signals.
VALUES = [10_000, 50_000, 90_000, 130_000, 180_000, 220_000, 280_000, 350_000, 420_000, 500_000]
SOURCE_ID = "ons.mid_year_estimates"


async def _seed_places_and_values() -> str:
    """Seed 10 LTLAs with population.total values from 10k to 500k.

    Includes a loader_run row. Returns the place_id with the lowest value.
    """
    engine = get_engine()
    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.trend_point"))
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM data.loader_run"))
        await conn.execute(text("DELETE FROM geography.postcode"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))

        for i, _val in enumerate(VALUES):
            place_id = f"ltla24:PLACE{i:02d}"
            code = f"E0{i:07d}"
            await conn.execute(
                text(
                    "INSERT INTO geography.place (id, type, code, name) "
                    "VALUES (:id, 'ltla24', :code, :name)"
                ),
                {"id": place_id, "code": code, "name": f"Place {i}"},
            )

        run = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO data.loader_run "
                "(id, source_id, started_at, finished_at, status, rows_written) "
                "VALUES (:id, :src, :s, :f, 'ok', 10)"
            ),
            {"id": run, "src": SOURCE_ID, "s": now - timedelta(minutes=5), "f": now},
        )

        for i, val in enumerate(VALUES):
            place_id = f"ltla24:PLACE{i:02d}"
            await conn.execute(
                text(
                    "INSERT INTO data.indicator_value "
                    "(place_id, indicator_key, period, value, source_id, retrieved_at, caveats) "
                    "VALUES (:pid, 'population.total', '2024', :v, :src, :ret, '[]'::jsonb)"
                ),
                {"pid": place_id, "v": val, "src": SOURCE_ID, "ret": now},
            )

    return "ltla24:PLACE00"


async def test_extreme_percentile_bottom() -> None:
    """Lowest place (10k) gets extreme_percentile signal with severity='extreme'."""
    engine = get_engine()
    lowest_place_id = await _seed_places_and_values()

    result = await detect_insights(
        DetectInsightsInput(place_id=lowest_place_id),
        engine,
    )

    extreme_signals = [
        s for s in result.signals if s.kind == "extreme_percentile" and s.severity == "extreme"
    ]
    assert len(extreme_signals) == 1
    assert extreme_signals[0].indicator_key == "population.total"


async def test_extreme_percentile_top() -> None:
    """Highest place (500k) gets extreme_percentile signal with severity='extreme'."""
    engine = get_engine()
    await _seed_places_and_values()

    result = await detect_insights(
        DetectInsightsInput(place_id="ltla24:PLACE09"),
        engine,
    )

    extreme_signals = [
        s for s in result.signals if s.kind == "extreme_percentile" and s.severity == "extreme"
    ]
    assert len(extreme_signals) == 1
    assert extreme_signals[0].indicator_key == "population.total"


async def test_no_signals_for_median_place() -> None:
    """Middle place (180k) gets no extreme_percentile signals."""
    engine = get_engine()
    await _seed_places_and_values()

    result = await detect_insights(
        DetectInsightsInput(place_id="ltla24:PLACE04"),
        engine,
    )

    percentile_signals = [s for s in result.signals if s.kind == "extreme_percentile"]
    assert len(percentile_signals) == 0


async def test_empty_signals_when_no_data() -> None:
    """No data → empty signals."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.trend_point"))
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM data.loader_run"))
        await conn.execute(text("DELETE FROM geography.postcode"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))

    result = await detect_insights(
        DetectInsightsInput(place_id="ltla24:NODATA"),
        engine,
    )

    assert result.signals == []


async def _seed_proportion_with_one_bad_value() -> None:
    """Nine plausible employment rates and one percentage stored by mistake."""
    engine = get_engine()
    now = datetime.now(tz=UTC)
    rates = [0.62, 0.65, 0.67, 0.69, 0.71, 0.72, 0.74, 0.76, 0.80, 75.0]
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.trend_point"))
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM geography.postcode"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        for i, rate in enumerate(rates):
            place_id = f"ltla24:RATE{i:02d}"
            await conn.execute(
                text(
                    "INSERT INTO geography.place (id, type, code, name) "
                    "VALUES (:id, 'ltla24', :code, :name)"
                ),
                {"id": place_id, "code": f"E1{i:07d}", "name": f"Rate {i}"},
            )
            await conn.execute(
                text(
                    "INSERT INTO data.indicator_value "
                    "(place_id, indicator_key, period, value, source_id, retrieved_at, caveats) "
                    "VALUES (:pid, 'economy.employment_rate', '2024', :v, 'ons.aps', :ret, "
                    "'[]'::jsonb)"
                ),
                {"pid": place_id, "v": rate, "ret": now},
            )


async def test_values_breaking_their_contract_raise_no_signal() -> None:
    """A "proportion" of 75 is a storage error, not an extreme place."""
    await _seed_proportion_with_one_bad_value()

    result = await detect_insights(DetectInsightsInput(place_id="ltla24:RATE09"), get_engine())

    assert result.signals == []


async def test_bad_peer_values_do_not_shift_other_places_percentiles() -> None:
    """With the 75.0 excluded, 0.80 is the highest employment rate."""
    await _seed_proportion_with_one_bad_value()

    result = await detect_insights(DetectInsightsInput(place_id="ltla24:RATE08"), get_engine())

    top = [s for s in result.signals if s.kind == "extreme_percentile"]
    assert len(top) == 1
    assert top[0].severity == "extreme"
