"""Integration tests for the observations_summary field of get_place_profile.

Requires a running Postgres test database (``soundings_test`` on port 5433)
with migrations applied and the theme seed run. Each test seeds its own
organisation + place + observation rows and cleans up afterwards so there is
no cross-test pollution.
"""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.mhclg_imd2025.adapter import MhclgImd2025Adapter
from soundings.adapters.ons_mid_year_estimates.adapter import OnsMidYearEstimatesAdapter
from soundings.db.engine import get_engine
from soundings.orchestration.orchestrator import IndicatorOrchestrator
from soundings.orchestration.registry import AdapterRegistry
from soundings.tools.get_place_profile import GetPlaceProfileInput, get_place_profile

pytestmark = pytest.mark.integration

# Reusable IDs that are unlikely to collide with seeded data.
_ORG_ID = "test-org-placeprofile-obs"
_PLACE_ID = "ltla24:E0PPOBS"
_PLACE_NAME = "Place Profile Obs Test Place"
_EMPTY_PLACE_ID = "ltla24:E0PPOBS2"
_EMPTY_PLACE_NAME = "Place Profile Obs Empty Place"


async def _seed_org_and_place(
    engine: AsyncEngine,
    *,
    place_id: str = _PLACE_ID,
    place_name: str = _PLACE_NAME,
) -> None:
    """Insert a test organisation and place (idempotent within a test).

    Ensures the themes used by these tests exist in catalogue.theme.
    """
    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        for theme_key in ("housing", "health"):
            await conn.execute(
                text(
                    "INSERT INTO catalogue.theme (key, label, description) "
                    "VALUES (:k, :label, :desc) ON CONFLICT (key) DO NOTHING"
                ),
                {"k": theme_key, "label": theme_key, "desc": ""},
            )
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) "
                "VALUES (:id, 'ltla24', :code, :name) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": place_id, "code": place_id.split(":")[1], "name": place_name},
        )
        await conn.execute(
            text(
                "INSERT INTO data.organisation "
                "(id, name, classification, source_id, retrieved_at, raw) "
                "VALUES (:id, :name, ARRAY[]::varchar[], 'ons.geography', :ret, '{}'::jsonb) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": _ORG_ID, "name": "Test Org For Place Profile Obs", "ret": now},
        )


async def _seed_observation(
    engine: AsyncEngine,
    *,
    theme: str,
    place_id: str = _PLACE_ID,
    org_id: str = _ORG_ID,
    statement: str = "A test observation statement for the place profile summary.",
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
                "VALUES (:id, :org, :place, :pstart, :theme, :stmt, NULL, NULL, NULL, "
                "        'qualitative', NULL, 'medium', :submitted)"
            ),
            {
                "id": obs_id,
                "org": org_id,
                "place": place_id,
                "pstart": date(2026, 1, 1),
                "theme": theme,
                "stmt": statement,
                "submitted": now,
            },
        )
    return obs_id


async def _cleanup(
    engine: AsyncEngine,
    *,
    place_ids: tuple[str, ...] = (_PLACE_ID, _EMPTY_PLACE_ID),
) -> None:
    """Remove test rows in FK-safe order."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM data.observation WHERE place_id = ANY(:places) OR organisation_id = :o"
            ),
            {"places": list(place_ids), "o": _ORG_ID},
        )
        await conn.execute(text("DELETE FROM data.organisation WHERE id = :o"), {"o": _ORG_ID})
        await conn.execute(
            text("DELETE FROM geography.place WHERE id = ANY(:places)"),
            {"places": list(place_ids)},
        )


def _build_orchestrator(engine: AsyncEngine) -> IndicatorOrchestrator:
    registry = AdapterRegistry(engine)
    registry.register("ons.mid_year_estimates", OnsMidYearEstimatesAdapter)
    registry.register("mhclg.imd2025", MhclgImd2025Adapter)
    return IndicatorOrchestrator(engine, registry)


async def test_place_profile_includes_observations_summary() -> None:
    """Seeding one observation for a place yields a non-None summary with
    the theme appearing with the correct count and org name."""
    engine = get_engine()
    await _cleanup(engine)
    await _seed_org_and_place(engine)
    await _seed_observation(engine, theme="housing")

    orchestrator = _build_orchestrator(engine)
    result = await get_place_profile(
        GetPlaceProfileInput(place_id=_PLACE_ID),
        orchestrator,
        engine,
    )
    try:
        assert result.observations_summary is not None
        summary = result.observations_summary
        assert summary.total_observations >= 1
        housing = next((t for t in summary.themes if t.theme == "housing"), None)
        assert housing is not None
        assert housing.count == 1
        assert "Test Org For Place Profile Obs" in housing.organisation_names
        assert housing.latest_submission is not None
    finally:
        await _cleanup(engine)


async def test_place_profile_observations_summary_none_when_empty() -> None:
    """A place with no observations returns observations_summary=None."""
    engine = get_engine()
    await _cleanup(engine)
    await _seed_org_and_place(engine, place_id=_EMPTY_PLACE_ID, place_name=_EMPTY_PLACE_NAME)

    orchestrator = _build_orchestrator(engine)
    result = await get_place_profile(
        GetPlaceProfileInput(place_id=_EMPTY_PLACE_ID),
        orchestrator,
        engine,
    )
    try:
        assert result.observations_summary is None
    finally:
        await _cleanup(engine)
