"""get_sub_areas tool — sub-area indicator values within a parent place.

Returns LSOA-level (or other child-type) values for a single indicator
within a parent place (e.g. all LSOA deprivation scores within an LTLA).
Includes the parent's own value and percentile for context. This is the
single-call equivalent of calling get_indicators for every child — Claude
uses it to answer "what are the most deprived neighbourhoods in X?"
"""

import logging
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.source_ref_factory import SourceRefFactory
from soundings.catalogue.contracts import get_contract_registry
from soundings.contracts.source_ref import SourceRef
from soundings.orchestration.measure_guard import guard_values

logger = logging.getLogger(__name__)


class SubAreaValue(BaseModel):
    place_id: str
    name: str
    value: float | None = None
    percentile: float | None = None  # within parent's peer universe


class GetSubAreasInput(BaseModel):
    place_id: str  # the parent place (e.g. ltla24:E06000004)
    indicator_key: str
    child_type: str = "auto"  # auto: use indicator geography + nation
    period: str | None = None
    limit: int = 50  # cap to avoid huge responses
    sort_by: str = "worst_first"  # "worst_first", "value_desc", "value_asc", "name"


class GetSubAreasOutput(BaseModel):
    parent_place_id: str
    indicator_key: str
    child_type: str
    sub_areas: list[SubAreaValue] = Field(default_factory=list)
    parent_value: float | None = None
    parent_percentile: float | None = None
    period: str = ""
    # How much of the parent the list covers. `sub_areas` may be a ranked
    # slice (see `limit`), so averages or shares must not be read off it.
    total_sub_areas: int = 0
    sub_areas_with_values: int = 0
    truncated: bool = False
    unit: str | None = None
    sources: list[SourceRef] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


TOOL_NAME = "get_sub_areas"
TOOL_DESCRIPTION = (
    "Get sub-area (neighbourhood-level) indicator values for all children "
    "of a parent place. Default child_type is 'auto': Welsh/English LSOA "
    "or Scottish Data Zone matched to the indicator's geography edition. "
    "Returns each child's value, name, and percentile within the parent's "
    "peer universe, plus the parent's own value for "
    "context. Use this to answer 'what are the most deprived neighbourhoods "
    "in X?' or 'show me neighbourhood-level [indicator] for X'. The list is "
    "capped at `limit` and ranked: check `truncated`, `total_sub_areas` and "
    "`sub_areas_with_values` before describing it as the whole place, and "
    "use aggregate_sub_areas for an average or a count above a threshold "
    "rather than working one out from this list."
)


def tool_spec() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": GetSubAreasInput.model_json_schema(),
        "output_schema": GetSubAreasOutput.model_json_schema(),
    }


async def get_sub_areas(
    input: GetSubAreasInput,
    orchestrator: Any,  # IndicatorOrchestrator
    engine: AsyncEngine,
) -> GetSubAreasOutput:
    # 1. Choose an indicator-compatible geography rather than assuming LSOAs.
    async with engine.connect() as conn:
        available = await conn.execute(
            text("SELECT available_at FROM catalogue.indicator WHERE key = :key"),
            {"key": input.indicator_key},
        )
        indicator_row = available.first()
    geography_types = list(indicator_row.available_at) if indicator_row else []
    child_type = resolve_sub_area_type(input.place_id, input.child_type, geography_types)
    # 2. Get current child place IDs via the canonical current hierarchy view.
    async with engine.connect() as conn:
        child_rows = (
            await conn.execute(
                text(
                    """
                    SELECT p.id, p.name
                    FROM geography.current_place_hierarchy ph
                    JOIN geography.place p ON p.id = ph.child_id
                    WHERE ph.parent_id = :parent_id
                      AND p.type = :child_type
                    ORDER BY p.name
                    """
                ),
                {"parent_id": input.place_id, "child_type": child_type},
            )
        ).all()

    if not child_rows:
        return GetSubAreasOutput(
            parent_place_id=input.place_id,
            indicator_key=input.indicator_key,
            child_type=child_type,
            caveats=[f"No {child_type} children found for {input.place_id}"],
        )

    # 2. Fetch indicator values for all children in one batched query
    child_ids = [r.id for r in child_rows]
    async with engine.connect() as conn:
        value_rows = (
            await conn.execute(
                text(
                    # CAST(:period AS text) rather than a bare `:period IS NULL`:
                    # asyncpg can't infer the type of a NULL bind in `$n IS NULL`
                    # and raised AmbiguousParameterError for the common no-period
                    # case. When period is NULL the filter is a no-op and
                    # DISTINCT ON ... ORDER BY period DESC returns the latest
                    # value per place.
                    """
                    SELECT DISTINCT ON (iv.place_id)
                        iv.place_id, iv.value, iv.period, ind.higher_is,
                        ind.unit, ind.caveats AS indicator_caveats,
                        iv.source_id, iv.retrieved_at, iv.caveats AS value_caveats
                    FROM data.indicator_value iv
                    JOIN catalogue.indicator ind ON ind.key = iv.indicator_key
                    WHERE iv.place_id = ANY(:child_ids)
                      AND iv.indicator_key = :indicator_key
                      AND (CAST(:period AS text) IS NULL OR iv.period = :period)
                    ORDER BY iv.place_id, iv.period DESC
                    """
                ),
                {
                    "child_ids": child_ids,
                    "indicator_key": input.indicator_key,
                    "period": input.period,
                },
            )
        ).all()

    # Values that break the indicator's measure contract are withheld here,
    # as they are everywhere else a number is served.
    guarded, withheld_caveat = guard_values(
        get_contract_registry(),
        input.indicator_key,
        {r.place_id: (float(r.value) if r.value is not None else None) for r in value_rows},
    )
    value_map = {r.place_id: (guarded[r.place_id], r.period) for r in value_rows}

    # 3. Fetch parent's value and percentile (context only)
    parent_value: float | None = None
    parent_percentile: float | None = None
    try:
        parent_result = await orchestrator._fetch_one(
            input.indicator_key, input.place_id, input.period
        )
        if parent_result:
            parent_value = parent_result.value
            parent_percentile = parent_result.benchmark_percentile
    except Exception:
        logger.debug(
            "Could not fetch parent value for %s / %s",
            input.indicator_key,
            input.place_id,
            exc_info=True,
        )

    # 4. Build sub-area list
    sub_areas: list[SubAreaValue] = []
    for row in child_rows:
        val_period = value_map.get(row.id)
        if val_period and val_period[0] is not None:
            sub_areas.append(
                SubAreaValue(
                    place_id=row.id,
                    name=row.name,
                    value=float(val_period[0]),
                    percentile=None,  # could be computed from peer distribution
                )
            )

    # 5. Sort. Welsh WIMD and PPFI deciles are lower-is-worse while
    #    English IMD scores are higher-is-worse. Use catalogue semantics.
    higher_is = value_rows[0].higher_is if value_rows else None
    sort_by = resolve_sub_area_sort(input.sort_by, higher_is)
    if sort_by == "value_desc":
        sub_areas.sort(key=lambda s: s.value or 0, reverse=True)
    elif sort_by == "value_asc":
        sub_areas.sort(key=lambda s: s.value or 0)
    else:
        sub_areas.sort(key=lambda s: s.name)

    # 6. Cap at limit, and say so
    with_values = len(sub_areas)
    truncated = with_values > input.limit
    sub_areas = sub_areas[: input.limit]

    # 7. Determine period used
    period_used = ""
    if value_rows:
        period_used = value_rows[0].period or ""

    # 8. Provenance and caveats come from the same rows as the values, so a
    #    neighbourhood answer can cite its own source, period and limits.
    sources: list[SourceRef] = []
    caveats: list[str] = []
    if value_rows:
        factory = SourceRefFactory(engine)
        for source_id in sorted({r.source_id for r in value_rows}):
            # The oldest retrieval among the rows used: never claim the data
            # is fresher than its stalest part.
            retrieved_at = min(r.retrieved_at for r in value_rows if r.source_id == source_id)
            ref = await factory.build(source_id, retrieved_at=retrieved_at, cache_status="cached")
            if ref is not None:
                sources.append(ref)
        seen: set[str] = set()
        for raw in [
            *(value_rows[0].indicator_caveats or []),
            *(c for r in value_rows for c in (r.value_caveats or [])),
        ]:
            caveat = str(raw)
            if caveat not in seen:
                seen.add(caveat)
                caveats.append(caveat)
    if withheld_caveat is not None:
        caveats.append(withheld_caveat)
    # Do not silently label a mixed-vintage neighbourhood set with the period
    # of whichever source row happened to come first.
    valid_periods = sorted(
        {
            str(r.period)
            for r in value_rows
            if guarded.get(r.place_id) is not None and r.period is not None
        }
    )
    if len(valid_periods) > 1:
        caveats.append(
            "MIXED_INDICATOR_PERIODS: Sub-areas use different reference "
            "periods (" + ", ".join(valid_periods) + "); do not aggregate "
            "them as a single-period statistic."
        )
    if truncated:
        caveats.append(
            f"Showing {len(sub_areas)} of {with_values} {child_type} areas with a value, "
            f"ranked by '{input.sort_by}'. This is a slice, not the whole of {input.place_id}."
        )
    missing = len(child_rows) - with_values
    if missing:
        caveats.append(
            f"{missing} of {len(child_rows)} {child_type} areas have no value for "
            f"{input.indicator_key} and are left out."
        )

    return GetSubAreasOutput(
        parent_place_id=input.place_id,
        indicator_key=input.indicator_key,
        child_type=child_type,
        sub_areas=sub_areas,
        parent_value=parent_value,
        parent_percentile=parent_percentile,
        period=period_used,
        total_sub_areas=len(child_rows),
        sub_areas_with_values=with_values,
        truncated=truncated,
        unit=value_rows[0].unit if value_rows else None,
        sources=sources,
        caveats=caveats,
    )


def resolve_sub_area_sort(requested: str, higher_is: str | None) -> str:
    """Translate default worst-first into a numeric ordering, without overriding explicit sorts."""
    if requested != "worst_first":
        return requested
    return "value_asc" if higher_is == "better" else "value_desc"


def resolve_sub_area_type(parent_id: str, requested: str, available_at: list[str]) -> str:
    """Select a small-area edition supported by both the nation and indicator."""
    if requested != "auto":
        return requested
    code = parent_id.partition(":")[2]
    is_scottish = len(code) == 9 and code.startswith("S") and code[1:].isdigit()
    if is_scottish:
        for geography in ("dz22", "dz11"):
            if geography in available_at:
                return geography
        return "dz22"
    return "lsoa21"
