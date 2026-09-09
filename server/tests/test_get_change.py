from datetime import UTC, datetime
from typing import Any

import pytest

import soundings.tools.get_change as change_module
from soundings.contracts.source_ref import SourceRef
from soundings.contracts.trend import Trend, TrendPoint
from soundings.tools.get_change import GetChangeInput, get_change
from soundings.tools.get_trend import GetTrendOutput


def _source() -> SourceRef:
    return SourceRef(
        source_id="test.temporal",
        source_label="Temporal test",
        publisher="Test",
        retrieved_at=datetime.now(tz=UTC),
        cache_status="cached",
        licence="CC0",
    )


@pytest.mark.asyncio
async def test_get_change_calculates_first_to_last_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()

    async def fake_get_trend(input: Any, orchestrator: Any) -> GetTrendOutput:
        return GetTrendOutput(
            trend=Trend(
                place_id=input.place_id,
                indicator=input.indicator,
                unit="people",
                points=[
                    TrendPoint(period="2020", value=100.0),
                    TrendPoint(period="2022", value=110.0),
                    TrendPoint(period="2024", value=125.0),
                ],
                source=source,
            ),
            sources=[source],
        )

    monkeypatch.setattr(change_module, "get_trend", fake_get_trend)
    result = await get_change(
        GetChangeInput(
            place_id="ltla24:E00000001",
            indicator="population.total",
            period_from="2020",
            period_to="2024",
        ),
        object(),
    )

    assert result.change is not None
    assert result.change.start.period == "2020"
    assert result.change.end.period == "2024"
    assert result.change.absolute_change == 25.0
    assert result.change.percentage_change == 25.0
    assert result.change.direction == "increase"
    assert result.change.start.temporal is not None
    assert result.change.start.temporal.granularity == "year"


@pytest.mark.asyncio
async def test_get_change_does_not_divide_by_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source()

    async def fake_get_trend(input: Any, orchestrator: Any) -> GetTrendOutput:
        return GetTrendOutput(
            trend=Trend(
                place_id=input.place_id,
                indicator=input.indicator,
                unit="count",
                points=[
                    TrendPoint(period="2023", value=0.0),
                    TrendPoint(period="2024", value=10.0),
                ],
                source=source,
            ),
            sources=[source],
        )

    monkeypatch.setattr(change_module, "get_trend", fake_get_trend)
    result = await get_change(
        GetChangeInput(place_id="ltla24:E00000001", indicator="test.metric"),
        object(),
    )

    assert result.change is not None
    assert result.change.percentage_change is None
    assert any("starting value is zero" in caveat for caveat in result.caveats)


@pytest.mark.asyncio
async def test_get_change_refuses_unorderable_periods(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source()

    async def fake_get_trend(input: Any, orchestrator: Any) -> GetTrendOutput:
        return GetTrendOutput(
            trend=Trend(
                place_id=input.place_id,
                indicator=input.indicator,
                unit="count",
                points=[
                    TrendPoint(period="2024", value=120.0),
                    TrendPoint(period="Baseline 2019", value=100.0),
                ],
                source=source,
            ),
            sources=[source],
        )

    monkeypatch.setattr(change_module, "get_trend", fake_get_trend)
    result = await get_change(
        GetChangeInput(place_id="ltla24:E00000001", indicator="test.metric"),
        object(),
    )

    assert result.change is None
    assert result.partial is True
    assert any("no comparable chronological extent" in caveat for caveat in result.caveats)
