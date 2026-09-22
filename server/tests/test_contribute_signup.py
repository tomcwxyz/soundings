"""Integration tests for POST /v1/contribute/signup.

Covers:
  - Creating a lightweight ``data.organisation`` row for orgs not in any
    official register (status "created", 201).
  - Idempotency: posting the same name twice returns 200 with status
    "exists" and the existing organisation_id.
  - The ``data.organisation_operates_in`` link row is created.

Marked ``@pytest.mark.integration`` because it requires a running Postgres
test database (``soundings_test`` on port 5433).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from soundings.app import app
from soundings.db.engine import get_engine

pytestmark = pytest.mark.integration

PLACE_ID = "ltla24:E06000005"
SIGNUP_NAME = "Acme Community Project"
EXPECTED_ORG_ID = "ctx.acme_community_project"


@pytest_asyncio.fixture(autouse=True)
async def _cleanup() -> AsyncIterator[None]:
    """Remove rows created by these tests so each test starts clean."""
    yield
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM data.organisation_operates_in WHERE organisation_id = :oid"),
            {"oid": EXPECTED_ORG_ID},
        )
        await conn.execute(
            text("DELETE FROM data.organisation WHERE id = :oid"),
            {"oid": EXPECTED_ORG_ID},
        )


async def _ensure_place():
    """Make sure the geography.place row the FK references exists."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) VALUES "
                "(:pid, 'ltla24', 'E06000005', 'Darlington') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"pid": PLACE_ID},
        )


async def test_signup_creates_organisation():
    await _ensure_place()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/v1/contribute/signup",
                json={
                    "name": SIGNUP_NAME,
                    "email": "contact@acme.example.org",
                    "primary_place_id": PLACE_ID,
                },
            )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "created"
    assert body["organisation_id"] == EXPECTED_ORG_ID
    assert body["organisation_id"].startswith("ctx.")

    # Verify the row landed in data.organisation with the manual source.
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT name, source_id FROM data.organisation WHERE id = :oid"),
                {"oid": EXPECTED_ORG_ID},
            )
        ).first()
    assert row is not None
    assert row.name == SIGNUP_NAME
    assert row.source_id == "ctx.manual_signup"


async def test_signup_rejects_duplicate():
    await _ensure_place()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            first = await ac.post(
                "/v1/contribute/signup",
                json={
                    "name": SIGNUP_NAME,
                    "email": "contact@acme.example.org",
                    "primary_place_id": PLACE_ID,
                },
            )
            second = await ac.post(
                "/v1/contribute/signup",
                json={
                    "name": SIGNUP_NAME,
                    "email": "contact@acme.example.org",
                    "primary_place_id": PLACE_ID,
                },
            )
    assert first.status_code == 201
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "exists"
    assert second.json()["organisation_id"] == EXPECTED_ORG_ID


async def test_signup_creates_operates_in_link():
    await _ensure_place()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.post(
                "/v1/contribute/signup",
                json={
                    "name": SIGNUP_NAME,
                    "email": "contact@acme.example.org",
                    "primary_place_id": PLACE_ID,
                },
            )

    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT place_id FROM data.organisation_operates_in "
                    "WHERE organisation_id = :oid"
                ),
                {"oid": EXPECTED_ORG_ID},
            )
        ).first()
    assert row is not None
    assert row.place_id == PLACE_ID
