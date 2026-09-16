"""Focused tests for funder hydration through the official 360Giving API."""

import httpx

from soundings.adapters.threesixtygiving.client import (
    DEFAULT_PAGE_SIZE,
    THREESIXTYGIVING_BASE,
    ThreeSixtyGivingClient,
)


async def test_iter_grants_made_uses_funder_endpoint_and_large_pages() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["accept"] = request.headers.get("accept", "")
        return httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "results": [{"grant_id": "g1", "data": {"id": "g1"}}],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = ThreeSixtyGivingClient(http_client=http)
        grants = [grant async for grant in client.iter_grants_made("GB-CHC-999")]

    assert grants[0]["grant_id"] == "g1"
    assert captured["url"] == (
        f"{THREESIXTYGIVING_BASE}/org/GB-CHC-999/grants_made/?limit={DEFAULT_PAGE_SIZE}"
    )
    assert captured["accept"] == "application/json"
