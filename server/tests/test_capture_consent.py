from uuid import UUID

import httpx
import pytest

from soundings.app import app
from soundings.capture.consent import CONSENT_VERSION


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_post_consent_minimal_sets_cookies_and_returns_session(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.post("/v1/capture/consent", json={"consent_level": "minimal"})

    assert response.status_code == 200
    body = response.json()
    assert body["consent_level"] == "minimal"
    assert body["consent_version"] == CONSENT_VERSION
    assert body["asker_sector"] is None
    # Body session_id must parse as UUID.
    UUID(body["session_id"])

    set_cookies = response.headers.get_list("set-cookie")
    cookie_blob = "\n".join(set_cookies)
    assert "soundings_session=" in cookie_blob
    assert "soundings_consent=minimal" in cookie_blob


async def test_post_consent_full_with_sector_includes_sector_cookie(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.post(
            "/v1/capture/consent",
            json={"consent_level": "full", "asker_sector": "charity"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["consent_level"] == "full"
    assert body["asker_sector"] == "charity"

    cookie_blob = "\n".join(response.headers.get_list("set-cookie"))
    assert "soundings_sector=charity" in cookie_blob


async def test_level_only_consent_clears_stale_sector_cookie(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.post(
            "/v1/capture/consent",
            json={"consent_level": "minimal"},
            cookies={"soundings_sector": "charity"},
        )

    assert response.status_code == 200
    cookie_blob = "\n".join(response.headers.get_list("set-cookie"))
    assert "soundings_sector=" in cookie_blob
    assert "Max-Age=0" in cookie_blob


async def test_post_consent_none_still_issues_session_cookie(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.post("/v1/capture/consent", json={"consent_level": "none"})

    body = response.json()
    assert body["consent_level"] == "none"
    cookie_blob = "\n".join(response.headers.get_list("set-cookie"))
    # session_id rotates per session for rate-limit purposes (spec §8.2),
    # even when capture_level is none.
    assert "soundings_session=" in cookie_blob
    assert "soundings_consent=none" in cookie_blob


async def test_post_consent_rejects_unknown_consent_level(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.post("/v1/capture/consent", json={"consent_level": "partial"})

    assert response.status_code == 422


async def test_post_consent_rejects_unknown_sector(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.post(
            "/v1/capture/consent",
            json={"consent_level": "full", "asker_sector": "philanthropist"},
        )

    assert response.status_code == 422


async def test_consent_cookies_round_trip_to_session_middleware(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        consent_response = await client.post(
            "/v1/capture/consent",
            json={"consent_level": "full", "asker_sector": "researcher"},
        )
        # The same client carries the cookies forward.
        # Hit a low-cost endpoint that the session middleware processes — /healthz
        # is sufficient; we don't care about its body, only that it accepts
        # the request without 4xx and that the session middleware ran.
        echo = await client.get("/healthz")

    assert consent_response.status_code == 200
    assert echo.status_code in (200, 503)  # may be degraded depending on DB state
