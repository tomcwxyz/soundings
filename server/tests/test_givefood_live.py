"""Nightly contract checks against Give Food's published location feeds.

These deliberately hit the real endpoints: mock-based CI cannot catch a
retired URL, an upstream schema change, or a broken published mirror.
"""

import httpx
import pytest

from soundings.adapters.givefood.client import (
    GIVEFOOD_HEADERS,
    LOCATIONS_URL,
    MIRROR_URL,
    _parse_locations,
)

pytestmark = pytest.mark.live


@pytest.mark.parametrize("url", [LOCATIONS_URL, MIRROR_URL])
async def test_live_distribution_location_endpoint(url: str) -> None:
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.get(url, headers=GIVEFOOD_HEADERS)
        response.raise_for_status()
        rows = _parse_locations(response.json())

    # These feeds are UK-wide datasets of distribution locations. Requiring
    # many valid coordinates catches accidental organisation-only responses,
    # HTML error pages, and incomplete/empty payloads.
    geocoded = [r for r in rows if r["lat"] is not None and r["lng"] is not None]
    assert len(geocoded) > 100
    assert all(-9 <= r["lng"] <= 3 and 49 <= r["lat"] <= 61 for r in geocoded[:100])
    assert any(r["name"] for r in geocoded)
