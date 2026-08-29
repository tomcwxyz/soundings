"""Live test for the current Find That Charity v1 API.

Marker `live` — runs nightly, not in PR CI. No API key required.

The v1 service still supports direct charity lookup but no longer exposes
the legacy filtered country search Soundings previously used. SC005336
(Volunteer Scotland) is the stable direct-lookup smoke subject.
"""

import pytest

from soundings.adapters.find_that_charity.client import FindThatCharityClient

pytestmark = pytest.mark.live


async def test_get_charity_returns_volunteer_scotland_details() -> None:
    """Direct lookup of SC005336."""
    client = FindThatCharityClient()
    result = await client.get_charity("SC005336")

    assert result is not None, "no result returned for SC005336"
    assert result.id == "SC005336"
    assert result.name == "University Of Edinburgh"
    assert result.country == "Scotland"
