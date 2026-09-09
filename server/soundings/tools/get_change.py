"""get_change tool — deterministic change between two observations."""

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from soundings.contracts.source_ref import SourceRef
from soundings.contracts.temporal import TemporalExtent
from soundings.tools.get_trend import GetTrendInput, get_trend

if TYPE_CHECKING:
    from soundings.orchestration.orchestrator import IndicatorOrchestrator

ChangeDirection = Literal["increase", "decrease", "unchanged"]


class ChangePoint(BaseModel):
    period: str
    value: float
    temporal: TemporalExtent | None = None


class IndicatorChange(BaseModel):
    place_id: str
    indicator: str
    unit: str
    start: ChangePoint
    end: ChangePoint
    absolute_change: float
    percentage_change: float | None = None
    direction: ChangeDirection
    breaks_in_series: list[str] = Field(default_factory=list)
    source: SourceRef


class GetChangeInput(BaseModel):
    place_id: str
    indicator: str
    period_from: str | None = None
    period_to: str | None = None


class GetChangeOutput(BaseModel):
    change: IndicatorChange | None = None
    sources: list[SourceRef] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    partial: bool = False


TOOL_NAME = "get_change"
TOOL_DESCRIPTION = (
    "Calculate how one indicator changed for one place over a period. Uses the "
    "first and last usable observations in the requested window and returns "
    "absolute and percentage change with provenance. Prefer this over asking "
    "the language model to calculate change from trend values itself."
)


def tool_spec() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": GetChangeInput.model_json_schema(),
        "output_schema": GetChangeOutput.model_json_schema(),
    }


async def get_change(
    input: GetChangeInput,
    orchestrator: "IndicatorOrchestrator",
) -> GetChangeOutput:
    trend_result = await get_trend(
        GetTrendInput(
            place_id=input.place_id,
            indicator=input.indicator,
            period_from=input.period_from,
            period_to=input.period_to,
        ),
        orchestrator,
    )
    if trend_result.trend is None:
        return GetChangeOutput(
            change=None,
            sources=trend_result.sources,
            caveats=trend_result.caveats,
            partial=True,
        )

    usable = [point for point in trend_result.trend.points if point.value is not None]
    if len(usable) < 2:
        return GetChangeOutput(
            change=None,
            sources=trend_result.sources,
            caveats=[
                *trend_result.caveats,
                f"Need at least two observations to calculate change for {input.indicator}",
            ],
            partial=True,
        )

    unorderable = [
        point.period
        for point in usable
        if point.temporal is None or point.temporal.reference_start is None
    ]
    if unorderable:
        return GetChangeOutput(
            change=None,
            sources=trend_result.sources,
            caveats=[
                *trend_result.caveats,
                "Cannot calculate change safely because one or more observation "
                "periods have no comparable chronological extent: "
                + ", ".join(unorderable),
            ],
            partial=True,
        )

    first = usable[0]
    last = usable[-1]
    assert first.value is not None
    assert last.value is not None
    start_value = float(first.value)
    end_value = float(last.value)
    absolute_change = end_value - start_value
    percentage_change = (absolute_change / start_value) * 100.0 if start_value != 0 else None
    direction: ChangeDirection
    if absolute_change > 0:
        direction = "increase"
    elif absolute_change < 0:
        direction = "decrease"
    else:
        direction = "unchanged"

    caveats = list(trend_result.caveats)
    if start_value == 0:
        caveats.append("Percentage change is undefined because the starting value is zero")
    if trend_result.trend.breaks_in_series:
        caveats.append(
            "The requested interval contains a documented series break; "
            "interpret the change cautiously"
        )

    change = IndicatorChange(
        place_id=input.place_id,
        indicator=input.indicator,
        unit=trend_result.trend.unit,
        start=ChangePoint(period=first.period, value=start_value, temporal=first.temporal),
        end=ChangePoint(period=last.period, value=end_value, temporal=last.temporal),
        absolute_change=absolute_change,
        percentage_change=percentage_change,
        direction=direction,
        breaks_in_series=trend_result.trend.breaks_in_series,
        source=trend_result.trend.source,
    )
    return GetChangeOutput(
        change=change,
        sources=trend_result.sources or [trend_result.trend.source],
        caveats=caveats,
        partial=trend_result.partial,
    )
