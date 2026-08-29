"""End-to-end integration test for the observation contribution flow.

Exercises the full path:
  1. POST /v1/contribute/signup        — register a new organisation
  2. Sign the org_id cookie via MagicLinkService.sign_cookie_value
  3. POST /v1/observations             — submit an observation (authenticated)
  4. POST /v1/tools/get_observations   — query the observation back
  5. POST /v1/tools/get_place_profile — verify the observation appears in
     the place profile's observations_summary

Marked ``@pytest.mark.integration`` because it requires a running Postgres
test database (``soundings_test`` on port 5433) with migrations applied.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from soundings.app import app
from soundings.db.engine import get_engine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

THEME_KEY = "food_insecurity"
PLACE_ID = "ltla24:E06000004"
ORG_NAME = "Teesside Mutual Aid E2E"


async def _ensure_seed_data() -> None:
    """Insert the food_insecurity theme and the LTLA place if they are
    missing.  Both are required for the submission + profile queries.
    Idempotent so re-running is safe.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO catalogue.theme (key, label, description) VALUES "
                "(:key, 'Food Insecurity', 'Access to food, food bank usage, food poverty.') "
                "ON CONFLICT (key) DO NOTHING"
            ),
            {"key": THEME_KEY},
        )
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) VALUES "
                "(:pid, 'ltla24', 'E06000004', 'Middlesbrough') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"pid": PLACE_ID},
        )


@pytest_asyncio.fixture(autouse=True)
async def _cleanup() -> AsyncIterator[None]:
    """Remove rows created by this test so each run starts clean."""
    yield
    engine = get_engine()
    async with engine.begin() as conn:
        # Delete observations for our test org (by org name match on the
        # join) — simplest is to delete by organisation_id once we know it,
        # but cleanup runs after the test so we delete by name.
        await conn.execute(
            text(
                "DELETE FROM data.observation "
                "WHERE organisation_id IN ( "
                "  SELECT id FROM data.organisation WHERE name = :name "
                ")"
            ),
            {"name": ORG_NAME},
        )
        await conn.execute(
            text(
                "DELETE FROM data.organisation_operates_in "
                "WHERE organisation_id IN ( "
                "  SELECT id FROM data.organisation WHERE name = :name "
                ")"
            ),
            {"name": ORG_NAME},
        )
        await conn.execute(
            text("DELETE FROM data.organisation WHERE name = :name"),
            {"name": ORG_NAME},
        )


async def test_full_observation_flow() -> None:
    """signup -> authenticate -> submit -> query -> profile."""
    await _ensure_seed_data()

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            # ---------------------------------------------------------- #
            # 1. Sign up a new organisation.
            # ---------------------------------------------------------- #
            signup_resp = await ac.post(
                "/v1/contribute/signup",
                json={
                    "name": ORG_NAME,
                    "email": "contact@teesside-mutual-e2e.org",
                    "primary_place_id": PLACE_ID,
                },
            )
            assert signup_resp.status_code == 201, signup_resp.text
            org_id = signup_resp.json()["organisation_id"]

            # ---------------------------------------------------------- #
            # 2. Sign the org_id cookie using the app's MagicLinkService.
            # ---------------------------------------------------------- #
            service = app.state.magic_link_service
            signed_cookie = service.sign_cookie_value(org_id)
            ac.cookies.set("soundings_contrib_session", signed_cookie)

            # ---------------------------------------------------------- #
            # 3. Submit an observation.
            # ---------------------------------------------------------- #
            submit_resp = await ac.post(
                "/v1/observations",
                json={
                    "organisation_id": org_id,
                    "place_id": PLACE_ID,
                    "period_start": "2026-08-01",
                    "theme": THEME_KEY,
                    "statement": (
                        "We distributed 340 food parcels in August 2026, "
                        "up from 210 in August 2025."
                    ),
                    "value": 340,
                    "unit": "food parcels",
                    "evidence_type": "quantitative",
                    "methodology_note": "Monthly food bank distribution records.",
                    "confidence": "high",
                },
            )
            assert submit_resp.status_code == 201, submit_resp.text
            assert submit_resp.json()["status"] == "accepted"
            assert "observation_id" in submit_resp.json()

            # ---------------------------------------------------------- #
            # 4. Query via get_observations tool.
            # ---------------------------------------------------------- #
            obs_resp = await ac.post(
                "/v1/tools/get_observations",
                json={"place_id": PLACE_ID, "theme": THEME_KEY},
            )
            assert obs_resp.status_code == 200, obs_resp.text
            obs_data = obs_resp.json()
            assert obs_data["total"] >= 1
            observation = obs_data["observations"][0]
            assert observation["organisation_name"] == ORG_NAME
            assert observation["theme"] == THEME_KEY
            assert observation["value"] == 340
            assert observation["place_name"] == "Middlesbrough"

            # ---------------------------------------------------------- #
            # 5. Verify it appears in the place profile summary.
            # ---------------------------------------------------------- #
            profile_resp = await ac.post(
                "/v1/tools/get_place_profile",
                json={"place_id": PLACE_ID},
            )
            assert profile_resp.status_code == 200, profile_resp.text
            profile = profile_resp.json()
            assert profile["observations_summary"] is not None
            assert profile["observations_summary"]["total_observations"] >= 1
            theme_item = next(
                t for t in profile["observations_summary"]["themes"] if t["theme"] == THEME_KEY
            )
            assert theme_item["count"] >= 1
            assert ORG_NAME in theme_item["organisation_names"]
