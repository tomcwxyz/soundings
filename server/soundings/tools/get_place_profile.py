"""get_place_profile tool — baseline summary of a place per spec §4.2.

Resolves `include` domains to indicator keys via catalogue prefix match,
then fans out via the orchestrator. Per-domain failures become caveats,
not errors.
"""

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.contracts.indicator_value import IndicatorValue
from soundings.contracts.observation import (
    ObservationSummary,
    ObservationSummaryItem,
)
from soundings.contracts.source_ref import SourceRef
from soundings.orchestration.errors import GeographyNotFoundError
from soundings.orchestration.orchestrator import IndicatorOrchestrator


class PlaceHeader(BaseModel):
    id: str
    name: str
    type: str


class GetPlaceProfileInput(BaseModel):
    place_id: str
    include: list[str] = Field(default_factory=list)


class GetPlaceProfileOutput(BaseModel):
    place: PlaceHeader
    indicators: list[IndicatorValue] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    partial: bool = False
    observations_summary: ObservationSummary | None = None


async def get_place_profile(
    input: GetPlaceProfileInput,
    orchestrator: IndicatorOrchestrator,
    engine: AsyncEngine,
) -> GetPlaceProfileOutput:
    header = await _resolve_place_header(engine, input.place_id)
    indicator_keys = await _resolve_indicators(engine, input.include)
    result = await orchestrator.fetch(
        indicator_keys=indicator_keys,
        place_id=input.place_id,
        period=None,
    )
    enriched = await _enrich(engine, input.place_id, result.values)
    observations_summary = await _observations_summary(engine, input.place_id)
    return GetPlaceProfileOutput(
        place=header,
        indicators=enriched,
        sources=result.sources,
        caveats=result.caveats,
        partial=result.partial,
        observations_summary=observations_summary,
    )


async def _enrich(
    engine: AsyncEngine,
    place_id: str,
    indicators: list[IndicatorValue],
) -> list[IndicatorValue]:
    """Attach `higher_is` (from catalogue) and `benchmark_percentile`
    (vs same-type peers, excluding self) to each indicator.

    Two queries total, regardless of how many indicators are in the
    profile: one against `catalogue.indicator`, one against
    `data.indicator_value`. Returns new IndicatorValue instances; does
    not mutate the input.
    """
    if not indicators:
        return []

    keys = [ind.indicator for ind in indicators]
    directions = await _lookup_higher_is(engine, keys)
    keys_with_values = [(ind.indicator, ind.value) for ind in indicators if ind.value is not None]
    percentiles = await _compute_percentiles(engine, place_id, keys_with_values)

    return [
        ind.model_copy(
            update={
                "higher_is": directions.get(ind.indicator),
                "benchmark_percentile": percentiles.get(ind.indicator),
            }
        )
        for ind in indicators
    ]


async def _lookup_higher_is(
    engine: AsyncEngine,
    indicator_keys: list[str],
) -> dict[str, str | None]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT key, higher_is FROM catalogue.indicator WHERE key = ANY(:keys)"),
                {"keys": indicator_keys},
            )
        ).all()
    return {r.key: r.higher_is for r in rows}


async def _compute_percentiles(
    engine: AsyncEngine,
    place_id: str,
    keys_with_values: list[tuple[str, float]],
) -> dict[str, float]:
    """Single batched query: peer-type-filtered, self-excluded percentile
    for every (indicator, value) pair.

    Indicators with zero peers in `data.indicator_value` (passthrough-only
    sources, or peer universes that haven't been loaded yet) are omitted
    from the result.
    """
    if not keys_with_values:
        return {}

    keys = [k for k, _ in keys_with_values]
    values = [v for _, v in keys_with_values]
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    WITH queried AS (
                        SELECT * FROM unnest(:keys ::text[], :vals ::float8[])
                            AS t(indicator_key, value)
                    ),
                    ptype AS (
                        SELECT type FROM geography.place WHERE id = :place_id
                    ),
                    peers AS (
                        SELECT iv.indicator_key, iv.value
                        FROM data.indicator_value iv
                        JOIN geography.place p ON p.id = iv.place_id
                        WHERE iv.place_id <> :place_id
                          AND iv.indicator_key = ANY(:keys)
                          AND iv.value IS NOT NULL
                          AND p.type = (SELECT type FROM ptype)
                    )
                    SELECT
                        q.indicator_key,
                        COUNT(p.value) FILTER (WHERE p.value < q.value) AS below,
                        COUNT(p.value) AS total
                    FROM queried q
                    LEFT JOIN peers p ON p.indicator_key = q.indicator_key
                    GROUP BY q.indicator_key
                    """
                ),
                {"keys": keys, "vals": values, "place_id": place_id},
            )
        ).all()
    return {r.indicator_key: (r.below / r.total) * 100 for r in rows if r.total and r.total > 0}


async def _resolve_place_header(engine: AsyncEngine, place_id: str) -> PlaceHeader:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT id, name, type FROM geography.place WHERE id = :id"),
                {"id": place_id},
            )
        ).first()
    if row is None:
        raise GeographyNotFoundError(place_id)
    return PlaceHeader(id=row.id, name=row.name, type=row.type)


async def _observations_summary(engine: AsyncEngine, place_id: str) -> ObservationSummary | None:
    """Per-theme aggregate of observations submitted for ``place_id``.

    Groups ``data.observation`` by theme, joining ``data.organisation`` for
    org names. Returns ``None`` when the place has no observations, so a place
    page can show "no local observations yet" instead of an empty summary.
    """
    sql = text(
        """
        SELECT
            o.theme,
            count(*) AS count,
            max(o.submitted_at) AS latest,
            array_agg(DISTINCT org.name) AS org_names
        FROM data.observation o
        JOIN data.organisation org ON org.id = o.organisation_id
        WHERE o.place_id = :place_id
        GROUP BY o.theme
        ORDER BY latest DESC
        """
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(sql, {"place_id": place_id})).all()

    if not rows:
        return None

    items = [
        ObservationSummaryItem(
            theme=r.theme,
            count=r.count,
            latest_submission=r.latest,
            organisation_names=list(r.org_names or []),
        )
        for r in rows
    ]
    return ObservationSummary(
        total_observations=sum(i.count for i in items),
        themes=items,
    )


async def _resolve_indicators(engine: AsyncEngine, domains: list[str]) -> list[str]:
    if not domains:
        # Default: all top-level domains in the catalogue.
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT key FROM catalogue.indicator"))).all()
        return [r.key for r in rows]

    like_patterns = [f"{d}.%" for d in domains]
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT key FROM catalogue.indicator "
                    "WHERE key LIKE ANY(:patterns) OR key = ANY(:exact)"
                ),
                {"patterns": like_patterns, "exact": list(domains)},
            )
        ).all()
    return [r.key for r in rows]


TOOL_NAME = "get_place_profile"
TOOL_DESCRIPTION = (
    "Baseline summary of a place: returns the indicators in the requested "
    "domains, with provenance and caveats."
)


def tool_spec() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": GetPlaceProfileInput.model_json_schema(),
        "output_schema": GetPlaceProfileOutput.model_json_schema(),
    }
