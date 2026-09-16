"""get_trend tool — time series for one indicator at one place.

Loader-mode series now treat ``data.indicator_value`` as the canonical
observation store. The older ``data.trend_point`` table remains useful for its
``revised`` flag and compatibility, but a loader no longer has to mirror values
there before Soundings can answer a trend question.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from sqlalchemy import text

from soundings.contracts.source_ref import SourceRef
from soundings.contracts.temporal import period_in_window, period_sort_key
from soundings.contracts.trend import Trend, TrendPoint
from soundings.orchestration.errors import IndicatorNotRegisteredError

if TYPE_CHECKING:
    from soundings.orchestration.orchestrator import IndicatorOrchestrator


class GetTrendInput(BaseModel):
    place_id: str
    indicator: str
    period_from: str | None = None
    period_to: str | None = None


class GetTrendOutput(BaseModel):
    trend: Trend | None = None
    sources: list[SourceRef] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    partial: bool = False


TOOL_NAME = "get_trend"
TOOL_DESCRIPTION = (
    "Return the time series for one indicator at one place, optionally "
    "windowed by period_from / period_to. Period labels are returned with "
    "structured temporal semantics. Loader-mode series come from canonical "
    "indicator observations even when no trend_point mirror exists."
)


def tool_spec() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": GetTrendInput.model_json_schema(),
        "output_schema": GetTrendOutput.model_json_schema(),
    }


async def get_trend(input: GetTrendInput, orchestrator: "IndicatorOrchestrator") -> GetTrendOutput:
    # Keep the orchestrator call first: it owns level enforcement, adapter
    # registration errors and passthrough dispatch.
    result = await orchestrator.get_trend(
        indicator_key=input.indicator,
        place_id=input.place_id,
        period_from=input.period_from,
        period_to=input.period_to,
    )

    try:
        adapter = await orchestrator._registry.adapter_for_indicator(input.indicator)
    except IndicatorNotRegisteredError:
        return _output_from_result(result, input.period_from, input.period_to)

    if getattr(adapter, "mode", "loader") != "loader":
        return _output_from_result(result, input.period_from, input.period_to)

    # Do not bypass an explicit geography/availability failure merely because a
    # stale row happens to exist in the database.
    if any("INDICATOR_NOT_AVAILABLE_AT_LEVEL" in caveat for caveat in result.caveats):
        return _output_from_result(result, input.period_from, input.period_to)

    canonical = await _loader_observation_trend(
        orchestrator,
        adapter,
        indicator=input.indicator,
        place_id=input.place_id,
        period_from=input.period_from,
        period_to=input.period_to,
        fallback_source=result.trend.source if result.trend is not None else None,
    )
    if canonical is None:
        return _output_from_result(result, input.period_from, input.period_to)

    catalogue_caveats = await orchestrator._load_indicator_caveats(input.indicator)
    general, breaks = _split_series_breaks(catalogue_caveats)
    canonical.breaks_in_series = breaks
    return GetTrendOutput(
        trend=canonical,
        sources=[canonical.source],
        caveats=[f"{input.indicator}: {c}" for c in general],
        partial=False,
    )


async def _loader_observation_trend(
    orchestrator: "IndicatorOrchestrator",
    adapter: object,
    *,
    indicator: str,
    place_id: str,
    period_from: str | None,
    period_to: str | None,
    fallback_source: SourceRef | None,
) -> Trend | None:
    async with orchestrator._engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT iv.period, iv.value, iv.retrieved_at, ind.unit, "
                    "COALESCE(tp.revised, false) AS revised "
                    "FROM data.indicator_value iv "
                    "JOIN catalogue.indicator ind ON ind.key = iv.indicator_key "
                    "LEFT JOIN data.trend_point tp "
                    "ON tp.place_id = iv.place_id "
                    "AND tp.indicator_key = iv.indicator_key "
                    "AND tp.period = iv.period "
                    "WHERE iv.place_id = :pid AND iv.indicator_key = :ik"
                ),
                {"pid": place_id, "ik": indicator},
            )
        ).all()

    if not rows:
        return None

    points = [
        TrendPoint(
            period=str(row.period),
            value=float(row.value) if row.value is not None else None,
            revised=bool(row.revised),
        )
        for row in rows
        if period_in_window(str(row.period), period_from, period_to)
    ]
    points.sort(key=lambda point: period_sort_key(point.period))
    if not points:
        return None

    source = fallback_source
    if source is None:
        retrieved_at = max(row.retrieved_at for row in rows)
        source = adapter.get_source_ref(retrieved_at=retrieved_at, cache_status="cached")  # type: ignore[attr-defined]

    return Trend(
        place_id=place_id,
        indicator=indicator,
        unit=str(rows[0].unit),
        points=points,
        source=source,
    )


def _output_from_result(
    result: object,
    period_from: str | None,
    period_to: str | None,
) -> GetTrendOutput:
    trend = result.trend  # type: ignore[attr-defined]
    if trend is not None:
        trend.points = [
            point
            for point in trend.points
            if period_in_window(point.period, period_from, period_to)
        ]
        trend.points.sort(key=lambda point: period_sort_key(point.period))
    return GetTrendOutput(
        trend=trend,
        sources=result.sources,  # type: ignore[attr-defined]
        caveats=result.caveats,  # type: ignore[attr-defined]
        partial=result.partial,  # type: ignore[attr-defined]
    )


def _split_series_breaks(caveats: list[str]) -> tuple[list[str], list[str]]:
    prefix = "series_break:"
    general: list[str] = []
    breaks: list[str] = []
    for caveat in caveats:
        if caveat.startswith(prefix):
            breaks.append(caveat[len(prefix) :].strip())
        else:
            general.append(caveat)
    return general, breaks
