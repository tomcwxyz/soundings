"""Unit tests for the current Find That Charity v1 client."""

import httpx
import pytest

from soundings.adapters.find_that_charity.client import CharityDetail, FindThatCharityClient


async def test_get_charity_returns_detail() -> None:
    """GET /api/v1/charities/{id} unwraps the v1 result envelope."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/charities/GB-CHC-1145080"
        return httpx.Response(
            200,
            json={
                "success": True,
                "error": None,
                "result": {
                    "id": "GB-CHC-1145080",
                    "name": "The Royal British Legion",
                    "date_registered": "2011-11-11",
                    "postcode": "SW1A 1AA",
                    "active": True,
                    "activities": "The relief of serving and former serving personnel",
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = FindThatCharityClient(http_client=http)
        result = await client.get_charity("GB-CHC-1145080")

    assert result is not None
    assert isinstance(result, CharityDetail)
    assert result.id == "GB-CHC-1145080"
    assert result.name == "The Royal British Legion"
    assert result.country == "England/Wales"
    assert result.postcode == "SW1A 1AA"
    assert result.status == "Registered"


async def test_get_charity_returns_none_for_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(404, json={"detail": "Not found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = FindThatCharityClient(http_client=http)
        result = await client.get_charity("GB-CHC-999999")

    assert result is None


async def test_legacy_filtered_search_is_explicitly_unsupported() -> None:
    client = FindThatCharityClient()
    with pytest.raises(NotImplementedError, match="no longer exposes"):
        await client.search(country="Scotland", limit=10)


async def test_get_charity_handles_scottish_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/charities/SC005336"
        return httpx.Response(
            200,
            json={
                "success": True,
                "error": None,
                "result": {
                    "id": "GB-SC-SC005336",
                    "name": "Volunteer Scotland",
                    "date_registered": "2001-04-12",
                    "postcode": "EH1 1EZ",
                    "active": True,
                    "activities": "Volunteer development",
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = FindThatCharityClient(http_client=http)
        result = await client.get_charity("SC005336")

    assert result is not None
    assert result.id == "SC005336"
    assert result.country == "Scotland"
