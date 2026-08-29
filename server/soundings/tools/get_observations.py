"""get_observations tool — public read-only observation query.

Per the observations MVP plan (docs/plans/2026-08-24-observations-mvp.md),
Task 8. This is a public (no-auth) tool that queries ``data.observation``
with optional filters (place, theme, indicator, organisation) and returns
the joined organisation + place names alongside each row. When a
``place_id`` is supplied a per-theme ``ObservationSummary`` is also built.

Uses raw SQL via ``sqlalchemy.text()`` — the same pattern used throughout
the codebase (see ``get_place_profile``, ``get_indicators``).
"""

from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.contracts.observation import (
    GetObservationsInput,
    GetObservationsOutput,
    ObservationRecord,
    ObservationSummary,
    ObservationSummaryItem,
)

_SELECT_SQL = """
    SELECT
        o.id,
        o.organisation_id,
        org.name      AS organisation_name,
        o.place_id,
        p.name        AS place_name,
        o.period_start,
        o.period_end,
        o.theme,
        o.statement,
        o.indicator_key,
        o.value,
        o.unit,
        o.evidence_type,
        o.methodology_note,
        o.confidence,
        o.submitted_at
    FROM data.observation o
    JOIN data.organisation org ON org.id = o.organisation_id
    JOIN geography.place    p   ON p.id  = o.place_id
"""

_SUMMARY_SQL = """
    SELECT
        o.theme,
        COUNT(*) AS count,
        MAX(o.submitted_at) AS latest_submission,
        ARRAY_AGG(DISTINCT org.name) AS organisation_names
    FROM data.observation o
    JOIN data.organisation org ON org.id = o.organisation_id
    WHERE o.place_id = :place_id
    GROUP BY o.theme
    ORDER BY o.theme
"""

COUNT_SQL = "SELECT COUNT(*) FROM data.observation o"


def _build_where(input: GetObservationsInput) -> tuple[str, dict[str, object]]:
    """Build the optional WHERE clause + bind params from the input filters.

    Returns a (clause, params) tuple; the clause is empty when no filters
    are set. ``limit`` is added separately by the caller because it is
    always present.
    """
    clauses: list[str] = []
    params: dict[str, object] = {}
    if input.place_id is not None:
        clauses.append("o.place_id = :place_id")
        params["place_id"] = input.place_id
    if input.theme is not None:
        clauses.append("o.theme = :theme")
        params["theme"] = input.theme
    if input.indicator_key is not None:
        clauses.append("o.indicator_key = :indicator_key")
        params["indicator_key"] = input.indicator_key
    if input.organisation_id is not None:
        clauses.append("o.organisation_id = :organisation_id")
        params["organisation_id"] = input.organisation_id
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _row_to_record(row: Row[Any]) -> ObservationRecord:
    """Build an ObservationRecord from a SQLAlchemy row."""
    return ObservationRecord(
        id=row.id,
        organisation_id=row.organisation_id,
        organisation_name=row.organisation_name,
        place_id=row.place_id,
        place_name=row.place_name,
        period_start=row.period_start,
        period_end=row.period_end,
        theme=row.theme,
        statement=row.statement,
        indicator_key=row.indicator_key,
        value=float(row.value) if row.value is not None else None,
        unit=row.unit,
        evidence_type=row.evidence_type,
        methodology_note=row.methodology_note,
        confidence=row.confidence,
        submitted_at=row.submitted_at,
    )


async def _build_summary(engine: AsyncEngine, place_id: str) -> ObservationSummary:
    """Build a per-theme ObservationSummary for a place.

    Runs one grouped aggregate query against ``data.observation`` joined
    to ``data.organisation`` for org names.
    """
    async with engine.connect() as conn:
        rows = (await conn.execute(text(_SUMMARY_SQL), {"place_id": place_id})).all()

    items = [
        ObservationSummaryItem(
            theme=r.theme,
            count=r.count,
            latest_submission=r.latest_submission,
            organisation_names=list(r.organisation_names or []),
        )
        for r in rows
    ]
    return ObservationSummary(
        total_observations=sum(i.count for i in items),
        themes=items,
    )


async def get_observations(
    input: GetObservationsInput, engine: AsyncEngine
) -> GetObservationsOutput:
    """Query observations with optional filters.

    1. Build a SQL query with optional WHERE clauses (place_id, theme,
       indicator_key, organisation_id).
    2. JOIN data.organisation (org name) and geography.place (place name).
    3. ORDER BY submitted_at DESC, LIMIT :limit.
    4. Build ObservationRecord objects from the rows.
    5. If place_id is provided, also build an ObservationSummary grouped by
       theme.
    6. Return GetObservationsOutput with observations, total, summary,
       caveats.
    """
    where, params = _build_where(input)
    params["limit"] = input.limit

    sql = text(_SELECT_SQL + where + " ORDER BY o.submitted_at DESC LIMIT :limit")

    async with engine.connect() as conn:
        rows = (await conn.execute(sql, params)).all()
        total = await conn.scalar(text(COUNT_SQL + where), params)

    observations = [_row_to_record(r) for r in rows]

    summary: ObservationSummary | None = None
    if input.place_id is not None:
        summary = await _build_summary(engine, input.place_id)

    caveats: list[str] = []
    if total is None:
        total = 0
        caveats.append("Observation count unavailable (returned 0).")

    return GetObservationsOutput(
        observations=observations,
        total=total,
        summary=summary,
        caveats=caveats,
    )


TOOL_NAME = "get_observations"
TOOL_DESCRIPTION = (
    "Query submitted observations about UK places. Filter by place, theme, "
    "indicator, or organisation. Returns observation records with org and "
    "place names; when a place_id is supplied, also returns a per-theme "
    "summary. Public and read-only."
)


def tool_spec() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": GetObservationsInput.model_json_schema(),
        "output_schema": GetObservationsOutput.model_json_schema(),
    }
