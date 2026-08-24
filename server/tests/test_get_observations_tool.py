"""Integration tests for the get_observations tool.

Requires a running Postgres test database (``soundings_test`` on port
5433) with migrations applied and the theme seed run.  Each test seeds
its own organisation + place + observation rows and cleans up afterwards
so there is no cross-test pollution.
"""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.contracts.observation import (
    GetObservationsInput,
    ObservationRecord,
    ObservationSummary,
)
from soundings.db.engine import get_engine
from soundings.tools.get_observations import get_observations

pytestmark = pytest.mark.integration

# Reusable IDs that are unlikely to collide with seeded data.
_ORG_ID = "test-org-getobs"
_PLACE_ID = "ltla24:E0GETOBS"
_PLACE_NAME = "Getobs Test Place"


async def _seed_org_and_place(engine: AsyncEngine) -> None:
    """Insert a test organisation and place (idempotent within a test).

    Also ensures the themes used by these tests exist in catalogue.theme —
    the test DB may not have a full theme seed (some tests delete rows), so
    we INSERT ... ON CONFLICT DO NOTHING to be self-contained.
    """
    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        # Ensure the themes we use exist (FK target).
        for theme_key in ("housing", "health"):
            await conn.execute(
                text(
                    "INSERT INTO catalogue.theme (key, label, description) "
                    "VALUES (:k, :label, :desc) ON CONFLICT (key) DO NOTHING"
                ),
                {"k": theme_key, "label": theme_key, "desc": ""},
            )
        # Place first (organisation may reference it via registered_address_place_id).
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) "
                "VALUES (:id, 'ltla24', :code, :name) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": _PLACE_ID, "code": "E0GETOBS", "name": _PLACE_NAME},
        )
        await conn.execute(
            text(
                "INSERT INTO data.organisation "
                "(id, name, classification, source_id, retrieved_at, raw) "
                "VALUES (:id, :name, ARRAY[]::varchar[], 'ons.geography', :ret, '{}'::jsonb) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": _ORG_ID, "name": "Test Org For GetObservations", "ret": now},
        )


async def _seed_observation(
    engine: AsyncEngine,
    *,
    theme: str,
    statement: str = "A test observation statement for the get_observations tool.",
    indicator_key: str | None = None,
    place_id: str = _PLACE_ID,
    org_id: str = _ORG_ID,
) -> str:
    """Insert one observation row and return its id."""
    obs_id = str(uuid4())
    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO data.observation "
                "(id, organisation_id, place_id, period_start, theme, statement, "
                " indicator_key, value, unit, evidence_type, methodology_note, "
                " confidence, submitted_at) "
                "VALUES (:id, :org, :place, :pstart, :theme, :stmt, :ind, NULL, NULL, "
                "        'qualitative', NULL, 'medium', :submitted)"
            ),
            {
                "id": obs_id,
                "org": org_id,
                "place": place_id,
                "pstart": date(2026, 1, 1),
                "theme": theme,
                "stmt": statement,
                "ind": indicator_key,
                "submitted": now,
            },
        )
    return obs_id


async def _cleanup(engine: AsyncEngine) -> None:
    """Remove test rows in FK-safe order."""
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM data.observation WHERE place_id = :p OR organisation_id = :o"),
            {"p": _PLACE_ID, "o": _ORG_ID},
        )
        await conn.execute(text("DELETE FROM data.organisation WHERE id = :o"), {"o": _ORG_ID})
        await conn.execute(text("DELETE FROM geography.place WHERE id = :p"), {"p": _PLACE_ID})


async def test_get_observations_by_place() -> None:
    """Seeding one observation and querying by place_id returns it with
    organisation_name and place_name populated from the JOINs."""
    engine = get_engine()
    await _cleanup(engine)
    await _seed_org_and_place(engine)
    await _seed_observation(engine, theme="housing")

    result = await get_observations(GetObservationsInput(place_id=_PLACE_ID), engine)
    try:
        assert result.total == 1
        assert len(result.observations) == 1
        obs: ObservationRecord = result.observations[0]
        assert obs.organisation_name == "Test Org For GetObservations"
        assert obs.place_name == _PLACE_NAME
        assert obs.theme == "housing"
        assert obs.organisation_id == _ORG_ID
        assert obs.place_id == _PLACE_ID
    finally:
        await _cleanup(engine)


async def test_get_observations_empty() -> None:
    """Querying a place with no observations returns total=0 and an empty list."""
    engine = get_engine()
    await _cleanup(engine)
    await _seed_org_and_place(engine)

    result = await get_observations(GetObservationsInput(place_id=_PLACE_ID), engine)
    try:
        assert result.total == 0
        assert result.observations == []
        # Summary is only built when place_id is provided; with zero rows it
        # should still be present but empty.
        assert result.summary is not None
        assert result.summary.total_observations == 0
        assert result.summary.themes == []
    finally:
        await _cleanup(engine)


async def test_get_observations_with_summary() -> None:
    """Two observations in different themes for the same place produce a
    summary with one entry per theme."""
    engine = get_engine()
    await _cleanup(engine)
    await _seed_org_and_place(engine)
    await _seed_observation(engine, theme="housing")
    await _seed_observation(engine, theme="health")

    result = await get_observations(GetObservationsInput(place_id=_PLACE_ID), engine)
    try:
        assert result.total == 2
        assert result.summary is not None
        summary: ObservationSummary = result.summary
        assert summary.total_observations == 2
        theme_keys = {item.theme for item in summary.themes}
        assert theme_keys == {"housing", "health"}
        for item in summary.themes:
            assert item.count == 1
            assert item.latest_submission is not None
            assert "Test Org For GetObservations" in item.organisation_names
    finally:
        await _cleanup(engine)


async def test_get_observations_filter_by_theme() -> None:
    """Seeding observations in two themes and filtering by one returns only
    observations in that theme."""
    engine = get_engine()
    await _cleanup(engine)
    await _seed_org_and_place(engine)
    await _seed_observation(engine, theme="housing")
    await _seed_observation(engine, theme="health")

    result = await get_observations(
        GetObservationsInput(place_id=_PLACE_ID, theme="housing"), engine
    )
    try:
        assert result.total == 1
        assert len(result.observations) == 1
        assert result.observations[0].theme == "housing"
    finally:
        await _cleanup(engine)
