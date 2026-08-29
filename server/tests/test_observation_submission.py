"""Integration tests for POST /v1/observations.

Covers:
  - Authenticated POST creates an observation in data.observation (201).
  - Missing auth cookie -> 401.
  - Bad theme -> 422.
  - Cookie organisation_id differs from submission organisation_id -> 403.

Marked ``@pytest.mark.integration`` because it requires a running Postgres
test database (``soundings_test`` on port 5433) with migration 0009 applied.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from soundings.app import app
from soundings.contribute.auth import MagicLinkService
from soundings.db.engine import get_engine

pytestmark = pytest.mark.integration

ORG_ID = "GB-CHC-4000000"
PLACE_ID = "ltla24:E06000005"
THEME_KEY = "housing"
SOURCE_ID = "contribute-submission-test"


async def _seed() -> None:
    """Insert minimal rows so FKs on data.observation are satisfied."""
    engine = get_engine()
    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO catalogue.source "
                "(id, label, publisher, licence, mode, rate_limit) VALUES "
                "(:sid, 'Submission Test', 'Test', 'OGL-3.0', 'loader', '{}'::jsonb) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"sid": SOURCE_ID},
        )
        await conn.execute(
            text(
                "INSERT INTO catalogue.theme (key, label, description) VALUES "
                "(:key, 'Housing', 'Housing affordability, homelessness.') "
                "ON CONFLICT (key) DO NOTHING"
            ),
            {"key": THEME_KEY},
        )
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) VALUES "
                "(:pid, 'ltla24', 'E06000005', 'Darlington') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"pid": PLACE_ID},
        )
        await conn.execute(
            text(
                "INSERT INTO data.organisation "
                "(id, name, classification, source_id, retrieved_at, raw) VALUES "
                "(:id, 'Test Org', ARRAY[]::varchar[], :sid, :now, '{}'::jsonb) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": ORG_ID, "sid": SOURCE_ID, "now": now},
        )


@pytest_asyncio.fixture(autouse=True)
async def _cleanup() -> AsyncIterator[None]:
    """Remove rows created by these tests so each test starts clean."""
    yield
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM data.observation WHERE organisation_id = :oid"),
            {"oid": ORG_ID},
        )
        await conn.execute(
            text("DELETE FROM data.organisation WHERE id = :oid"),
            {"oid": ORG_ID},
        )
        await conn.execute(
            text("DELETE FROM catalogue.theme WHERE key = :key"),
            {"key": THEME_KEY},
        )
        await conn.execute(
            text("DELETE FROM geography.place WHERE id = :pid"),
            {"pid": PLACE_ID},
        )
        await conn.execute(
            text("DELETE FROM catalogue.source WHERE id = :sid"),
            {"sid": SOURCE_ID},
        )


def _signed_cookie(org_id: str = ORG_ID) -> str:
    """Produce an HMAC-signed contributor cookie value for the given org."""
    service: MagicLinkService = app.state.magic_link_service
    return service.sign_cookie_value(org_id)


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "organisation_id": ORG_ID,
        "place_id": PLACE_ID,
        "period_start": "2026-01-01",
        "theme": THEME_KEY,
        "statement": "47% of private landlords in our area refuse tenants on benefits.",
        "value": 47,
        "unit": "percent",
        "evidence_type": "quantitative",
        "confidence": "high",
    }
    base.update(overrides)
    return base


async def test_submit_quantitative_observation() -> None:
    """Authenticated POST creates an observation, returns 201."""
    await _seed()
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"soundings_contrib_session": _signed_cookie()},
        ) as ac:
            response = await ac.post("/v1/observations", json=_payload())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert "observation_id" in body
    # Verify the row landed in data.observation.
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT theme, value, unit, evidence_type, confidence "
                    "FROM data.observation WHERE id = :oid"
                ),
                {"oid": body["observation_id"]},
            )
        ).first()
    assert row is not None
    assert row.theme == THEME_KEY
    assert float(row.value) == 47.0
    assert row.unit == "percent"
    assert row.evidence_type == "quantitative"
    assert row.confidence == "high"


async def test_submit_without_auth_rejected() -> None:
    """No cookie -> 401."""
    await _seed()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/v1/observations", json=_payload())
    assert response.status_code == 401, response.text


async def test_submit_with_bad_theme_rejected() -> None:
    """Invalid theme -> 422."""
    await _seed()
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"soundings_contrib_session": _signed_cookie()},
        ) as ac:
            response = await ac.post("/v1/observations", json=_payload(theme="nonexistent_theme"))
    assert response.status_code == 422, response.text


async def test_submit_on_behalf_of_other_org_rejected() -> None:
    """Cookie org differs from submission org -> 403."""
    await _seed()
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"soundings_contrib_session": _signed_cookie()},
        ) as ac:
            response = await ac.post(
                "/v1/observations",
                json=_payload(organisation_id="GB-CHC-9999999"),
            )
    assert response.status_code == 403, response.text


async def test_submit_with_invalid_cookie_rejected() -> None:
    """Tampered cookie (bad signature) -> 401."""
    await _seed()
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"soundings_contrib_session": f"{ORG_ID}.badsig"},
        ) as ac:
            response = await ac.post("/v1/observations", json=_payload())
    assert response.status_code == 401, response.text
