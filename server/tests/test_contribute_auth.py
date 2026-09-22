"""Integration tests for the magic-link contributor auth flow.

Covers:
  - POST /v1/contribute/request-link  — issues a (stub) magic link for a known org
  - POST /v1/contribute/verify-link  — exchanges a valid token for a signed cookie
  - 503 when the MagicLinkService is not configured on app.state
  - request-link does not reveal whether the org exists (same response either way)

Marked ``@pytest.mark.integration`` because it requires a running Postgres
test database (``soundings_test`` on port 5433) with migration 0009 applied.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

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


async def _seed_org() -> None:
    """Insert a minimal data.organisation row + dependencies so the FK on
    contribution.contributor_session.organisation_id is satisfied."""
    engine = get_engine()
    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO catalogue.source "
                "(id, label, publisher, licence, mode, rate_limit) VALUES "
                "('contribute-test', 'Contribute Test', 'Test', 'OGL-3.0', "
                "'loader', '{}'::jsonb) ON CONFLICT (id) DO NOTHING"
            ),
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
                "(id, name, classification, registered_address_place_id, "
                " source_id, retrieved_at, raw) VALUES "
                "(:id, 'Test Org', ARRAY[]::varchar[], :pid, "
                " 'contribute-test', :now, '{}'::jsonb) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": ORG_ID, "pid": PLACE_ID, "now": now},
        )


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_contributor_sessions() -> AsyncIterator[None]:
    yield
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM contribution.contributor_session WHERE organisation_id = :oid"),
            {"oid": ORG_ID},
        )
        await conn.execute(
            text("DELETE FROM data.organisation WHERE id = :oid"),
            {"oid": ORG_ID},
        )
        await conn.execute(
            text("DELETE FROM catalogue.source WHERE id = 'contribute-test'"),
        )
        await conn.execute(
            text("DELETE FROM geography.place WHERE id = :pid"),
            {"pid": PLACE_ID},
        )


# ------------------------------------------------------------------ #
# request-link
# ------------------------------------------------------------------ #


async def test_request_link_returns_link_sent_for_known_org() -> None:
    await _seed_org()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/v1/contribute/request-link",
                json={"organisation_id": ORG_ID, "email": "contrib@example.org"},
            )
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "link_sent"}


async def test_request_link_does_not_reveal_unknown_org() -> None:
    """An unknown organisation_id must return the same response shape
    as a known one so callers can't enumerate org IDs."""
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/v1/contribute/request-link",
                json={
                    "organisation_id": "GB-CHC-9999999",
                    "email": "nobody@example.org",
                },
            )
    assert response.status_code == 200
    assert response.json() == {"status": "link_sent"}


# ------------------------------------------------------------------ #
# verify-link
# ------------------------------------------------------------------ #


async def test_verify_link_sets_cookie_and_returns_org_id() -> None:
    await _seed_org()
    async with app.router.lifespan_context(app):
        # Create a session directly via the service to get the raw token.
        service: MagicLinkService = app.state.magic_link_service
        token = await service.create_session(ORG_ID, "contrib@example.org")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/v1/contribute/verify-link",
                json={"token": token},
            )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "verified"
    assert body["organisation_id"] == ORG_ID

    # The signed cookie must be set with the expected attributes.
    set_cookies = response.headers.get_list("set-cookie")
    cookie_blob = "\n".join(set_cookies)
    assert "soundings_contrib_session=" in cookie_blob
    assert "HttpOnly" in cookie_blob
    assert "samesite=strict" in cookie_blob.lower()
    assert "max-age=86400" in cookie_blob.lower()


async def test_verify_link_rejects_invalid_token() -> None:
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/v1/contribute/verify-link",
                json={"token": "not-a-real-token"},
            )
    assert response.status_code == 401


async def test_verify_link_rejects_already_used_token() -> None:
    await _seed_org()
    async with app.router.lifespan_context(app):
        service: MagicLinkService = app.state.magic_link_service
        token = await service.create_session(ORG_ID, "contrib@example.org")
        # First use succeeds.
        first = await service.verify_token(token)
        assert first == ORG_ID
        # Second use must fail.
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/v1/contribute/verify-link",
                json={"token": token},
            )
    assert response.status_code == 401


async def test_verify_link_rejects_expired_token() -> None:
    await _seed_org()
    engine = get_engine()
    async with app.router.lifespan_context(app):
        service: MagicLinkService = app.state.magic_link_service
        token = await service.create_session(ORG_ID, "contrib@example.org")
        # Force the row's created_at + expires_at into the past so the row is
        # expired.  We must update both because the check constraint
        # ``expires_at > created_at`` would otherwise reject the row.
        past = datetime.now(tz=UTC) - timedelta(hours=2)
        recent = past + timedelta(minutes=5)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE contribution.contributor_session "
                    "SET created_at = :past, expires_at = :recent "
                    "WHERE token_hash = :h"
                ),
                {"past": past, "recent": recent, "h": service._hash(token)},
            )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/v1/contribute/verify-link",
                json={"token": token},
            )
    assert response.status_code == 401


# ------------------------------------------------------------------ #
# 503 when MagicLinkService not configured
# ------------------------------------------------------------------ #


async def test_request_link_503_when_service_not_configured() -> None:
    saved = getattr(app.state, "magic_link_service", None)
    app.state.magic_link_service = None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/v1/contribute/request-link",
                json={"organisation_id": ORG_ID, "email": "x@example.org"},
            )
    finally:
        app.state.magic_link_service = saved
    assert response.status_code == 503


async def test_verify_link_503_when_service_not_configured() -> None:
    saved = getattr(app.state, "magic_link_service", None)
    app.state.magic_link_service = None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/v1/contribute/verify-link",
                json={"token": "anything"},
            )
    finally:
        app.state.magic_link_service = saved
    assert response.status_code == 503
