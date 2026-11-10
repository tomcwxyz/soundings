"""aggregate_sub_areas — a statistic over every sub-area of a place.

``get_sub_areas`` returns a ranked slice for display. Counting or averaging
off that slice undercounts whenever the place has more sub-areas than the
slice holds, which the 9 November 2026 baseline saw. This tool reads all of
them, applies the same measure-contract guard, and reports how many had a
value so coverage is never implied.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.source_ref_factory import SourceRefFactory
from soundings.contracts.source_ref import SourceRef
from soundings.tools.get_sub_areas import GetSubAreasInput, get_sub_areas

Statistic = Literal[
    "count_at_or_below",
    "count_at_or_above",
    "mean",
    "population_weighted_mean",
    "min",
    "max",
]
_COUNTS = {"count_at_or_below", "count_at_or_above"}
# Large enough to cover the biggest local authority's LSOAs (Birmingham ~640).
_ALL = 5000


class AggregateSubAreasInput(BaseModel):
    place_id: str
    indicator_key: str
    statistic: Statistic
    threshold: float | None = Field(
        default=None, description="Required for the count statistics, e.g. 1 for decile 1."
    )
    child_type: str = "auto"
    period: str | None = None

    @model_validator(mode="after")
    def _threshold_for_counts(self) -> AggregateSubAreasInput:
        if self.statistic in _COUNTS and self.threshold is None:
            raise ValueError(f"{self.statistic} needs a threshold")
        return self


class AggregateSubAreasOutput(BaseModel):
    parent_place_id: str
    indicator_key: str
    child_type: str
    statistic: Statistic
    threshold: float | None = None
    result: float | None
    # For counts: result divided by the sub-areas that had a value.
    share_of_with_values: float | None = None
    n_with_values: int
    total_sub_areas: int
    unit: str | None = None
    period: str = ""
    working: str
    sources: list[SourceRef] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


async def aggregate_sub_areas(
    input: AggregateSubAreasInput,
    orchestrator: Any,
    engine: AsyncEngine,
) -> AggregateSubAreasOutput:
    rows = await get_sub_areas(
        GetSubAreasInput(
            place_id=input.place_id,
            indicator_key=input.indicator_key,
            child_type=input.child_type,
            period=input.period,
            limit=_ALL,
            sort_by="name",
        ),
        orchestrator,
        engine,
    )
    values = {s.place_id: s.value for s in rows.sub_areas if s.value is not None}
    sources = list(rows.sources)
    # The slice caveat does not apply: every sub-area was read.
    caveats = [c for c in rows.caveats if not c.startswith("Showing ")]
    result: float | None = None
    share: float | None = None
    n = len(values)

    if n == 0:
        working = "No sub-area had a value."
    elif input.statistic in _COUNTS:
        assert input.threshold is not None
        if input.statistic == "count_at_or_below":
            hits = sum(1 for v in values.values() if v <= input.threshold)
            relation = "at or below"
        else:
            hits = sum(1 for v in values.values() if v >= input.threshold)
            relation = "at or above"
        result, share = float(hits), hits / n
        working = (
            f"{hits} of {n} {rows.child_type} areas with a value are {relation} "
            f"{input.threshold:g} ({share:.1%})."
        )
    elif input.statistic == "mean":
        result = sum(values.values()) / n
        working = (
            f"Simple mean of {n} {rows.child_type} values: {result:.4g}. Each area counts "
            "equally whatever its population."
        )
    elif input.statistic == "population_weighted_mean":
        populations, retrieved_at = await _populations(engine, list(values))
        paired = [(v, populations[pid]) for pid, v in values.items() if populations.get(pid)]
        total = sum(p for _, p in paired)
        if total and retrieved_at is not None:
            result = sum(v * p for v, p in paired) / total
            n = len(paired)
            working = f"Mean of {n} {rows.child_type} values weighted by population: {result:.4g}."
            ref = await SourceRefFactory(engine).build(
                "ons.mid_year_estimates", retrieved_at=retrieved_at, cache_status="cached"
            )
            if ref is not None and all(s.source_id != ref.source_id for s in sources):
                sources.append(ref)
            if len(paired) < len(values):
                caveats.append(
                    f"{len(values) - len(paired)} sub-areas had no population and were left "
                    "out of the weighted mean."
                )
        else:
            working = "No population figures were available to weight by."
    elif input.statistic == "min":
        result = min(values.values())
        working = f"Lowest of {n} {rows.child_type} values: {result:g}."
    else:
        result = max(values.values())
        working = f"Highest of {n} {rows.child_type} values: {result:g}."

    return AggregateSubAreasOutput(
        parent_place_id=input.place_id,
        indicator_key=input.indicator_key,
        child_type=rows.child_type,
        statistic=input.statistic,
        threshold=input.threshold,
        result=result,
        share_of_with_values=share,
        n_with_values=len(values),
        total_sub_areas=rows.total_sub_areas,
        unit=rows.unit,
        period=rows.period,
        working=working,
        sources=sources,
        caveats=caveats,
    )


async def _populations(
    engine: AsyncEngine, place_ids: list[str]
) -> tuple[dict[str, float], datetime | None]:
    """Latest population per place, and the oldest retrieval among them."""
    async with engine.connect() as conn:
        found = (
            await conn.execute(
                text(
                    "SELECT DISTINCT ON (place_id) place_id, value, retrieved_at "
                    "FROM data.indicator_value "
                    "WHERE indicator_key = 'population.total' AND place_id = ANY(:ids) "
                    "AND value > 0 ORDER BY place_id, period DESC"
                ),
                {"ids": place_ids},
            )
        ).all()
    oldest = min((r.retrieved_at for r in found), default=None)
    return {r.place_id: float(r.value) for r in found}, oldest


def tool_spec() -> dict[str, object]:
    return {
        "name": "aggregate_sub_areas",
        "description": (
            "A statistic over every neighbourhood (or other sub-area) of a place for one "
            "indicator: how many are at or below / at or above a threshold (e.g. how many "
            "LSOAs are in IMD decile 1), the simple or population-weighted mean, the min or "
            "max. Use this rather than counting or averaging rows from get_sub_areas, which "
            "returns a ranked slice. Reports how many sub-areas had a value."
        ),
        "input_schema": AggregateSubAreasInput.model_json_schema(),
        "output_schema": AggregateSubAreasOutput.model_json_schema(),
    }
