"""Unit tests for PoliceUkClient (mock transport)."""

import httpx

from soundings.adapters.police_uk.client import POLICE_UK_BASE, PoliceUkClient


async def test_client_gets_crimes_with_query_params() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        return httpx.Response(
            200,
            json=[
                {
                    "category": "all-crime",
                    "location": {"latitude": "54.5705", "longitude": "-1.3198"},
                    "month": "2026-02",
                },
                {
                    "category": "all-crime",
                    "location": {"latitude": "54.5621", "longitude": "-1.3145"},
                    "month": "2026-02",
                },
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = PoliceUkClient(http_client=http)
        crimes = await client.get_crimes(
            category="all-crime",
            lat=54.5704,
            lng=-1.3187,
            date="2026-02",
        )

    url = str(captured["url"])
    assert url.startswith(f"{POLICE_UK_BASE}/crimes-street/all-crime")
    assert captured["method"] == "GET"
    assert "lat=54.5704" in url
    assert "lng=-1.3187" in url
    assert "date=2026-02" in url

    assert len(crimes) == 2
    assert crimes[0]["category"] == "all-crime"


async def test_client_omits_date_when_not_supplied() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = PoliceUkClient(http_client=http)
        await client.get_crimes(category="anti-social-behaviour", lat=54.0, lng=-1.0)

    assert "date=" not in str(captured["url"])
    assert "lat=54.0" in str(captured["url"])


async def test_client_retries_429_then_returns_results() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        del request
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0.001"})
        return httpx.Response(200, json=[{"category": "all-crime"}])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = PoliceUkClient(http_client=http)
        crimes = await client.get_crimes(category="all-crime", lat=54.0, lng=-1.0)

    assert attempts == 2
    assert crimes == [{"category": "all-crime"}]


async def test_client_returns_empty_list_on_404() -> None:
    """Police.uk returns 404 for points outside any force boundary —
    treat as 'no crimes here' rather than a fatal error."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(404, json={"message": "out of area"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = PoliceUkClient(http_client=http)
        crimes = await client.get_crimes(category="all-crime", lat=0.0, lng=0.0)
    assert crimes == []
