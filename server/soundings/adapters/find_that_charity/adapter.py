"""Find That Charity adapter — passthrough mode for cross-jurisdiction lookup.

This adapter previously provided organisation lookup for Scotland and Northern
Ireland. Find That Charity v1 removed the filtered country search endpoint that
implementation depended on, so place discovery is currently disabled rather
than returning misleading country-level results as if they were local.
England/Wales continues to use the Charity Commission loader.

Per Phase 4 Block C:
- source_id: "find_that_charity"
- mode: "passthrough"
- ttl: 168 hours (weekly)

Does NOT publish indicators. Direct FTC charity lookup remains available in
the client for enrichment/testing, but `fetch_organisations` returns [] until
Soundings has a genuine area-discovery source for Scotland and Northern Ireland.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.find_that_charity.client import FindThatCharityClient
from soundings.adapters.passthrough_base import PassthroughAdapter
from soundings.contracts.organisation import OrganisationRef

SOURCE_ID = "find_that_charity"
DEFAULT_TTL = timedelta(hours=168)


class FindThatCharityAdapter(PassthroughAdapter):
    source_id = SOURCE_ID

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        ttl: timedelta = DEFAULT_TTL,
        ftc_client: FindThatCharityClient | None = None,
        http_client: httpx.AsyncClient | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        super().__init__(engine, ttl=ttl, http_client=http_client)
        self._ftc = ftc_client or FindThatCharityClient(http_client=http_client)
        self._now = now

    async def fetch_organisations(
        self,
        place_id: str,
        filters: list[str] | None = None,
        limit: int = 50,
    ) -> list[OrganisationRef]:
        """Return organisations for a place via FTC.

        Routes based on place country (derived from place_id prefix):
        - Scotland -> country=Scotland
        - NI -> country=Northern Ireland
        - England/Wales -> returns [] (E&W goes via CC loader)
        """
        del filters, limit
        country = self._country_from_place_id(place_id)
        if country in {"Scotland", "Northern Ireland"}:
            # FTC v1 has direct lookup but no country/place discovery endpoint.
            # Returning no organisations is safer than presenting a national
            # slice as if it were specific to the requested local authority.
            return []
        return []

    def _country_from_place_id(self, place_id: str) -> str | None:
        """Derive country from place_id prefix — no DB query needed."""
        if place_id.startswith("country:S"):
            return "Scotland"
        if place_id.startswith("country:NI"):
            return "Northern Ireland"
        if place_id.startswith(("ltla24:S", "utla24:S")):
            return "Scotland"
        if place_id.startswith(("ltla24:N", "utla24:N")):
            return "Northern Ireland"

        # Default to England for English LTLAs/UTLAs/regions
        # We can't always know if it's Wales, but FTC handles both
        return "England"

    async def _call_upstream(self, client: httpx.AsyncClient, cache_key: str) -> None:
        """Not used — indicators are not published by this adapter."""
        raise NotImplementedError("FindThatCharityAdapter does not publish indicators")
