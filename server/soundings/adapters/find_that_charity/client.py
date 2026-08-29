"""Async HTTP client for the Find That Charity API.

Base: https://findthatcharity.uk/api/v1/

Public, no auth required. The current API exposes direct charity and
organisation lookup, but no longer exposes the legacy country/name/postcode
charity search endpoint Soundings originally used for place discovery.

- `GET /charities/{id}` — single-charity detail lookup by registered ID.
- `GET /organisations/{id}` — canonical organisation lookup.

Used by the FindThatCharityAdapter to provide organisation lookup for
Scotland and Northern Ireland (E&W uses the Charity Commission loader).
"""

from dataclasses import dataclass
from typing import Any

import httpx
from aiolimiter import AsyncLimiter

FIND_THAT_CHARITY_BASE = "https://findthatcharity.uk/api/v1"


@dataclass
class CharityDetail:
    """Full charity detail from FTC."""

    id: str  # e.g., "GB-CHC-123456" or "SC012345"
    name: str
    registered_date: str | None
    postcode: str | None
    country: str  # "England", "Wales", "Scotland", "Northern Ireland"
    status: str  # "Registered", "Removed", etc.
    activities: str | None
    charitable_objects: str | None
    source_url: str


@dataclass
class CharitySearchResult:
    """Single result from the FTC search endpoint."""

    id: str
    name: str
    postcode: str | None
    country: str


class FindThatCharityClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        *,
        rate_per_second: float = 4.0,
    ) -> None:
        self._client = http_client
        self._owns_client = http_client is None
        self._limiter = AsyncLimiter(max_rate=rate_per_second, time_period=1)

    async def get_charity(self, charity_id: str) -> CharityDetail | None:
        """Fetch detail for a single charity by its registered ID.

        Args:
            charity_id: The cross-regulator ID (e.g., "GB-CHC-123456",
                "SC012345", "NI123456").

        Returns:
            CharityDetail if found, None if 404.
        """
        async with self._limiter:
            client = self._client or httpx.AsyncClient(timeout=30.0)
            try:
                response = await client.get(f"{FIND_THAT_CHARITY_BASE}/charities/{charity_id}")
            finally:
                if self._owns_client:
                    await client.aclose()

        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        return self._parse_charity_detail(result, requested_id=charity_id)

    async def search(
        self,
        name: str | None = None,
        postcode: str | None = None,
        country: str | None = None,
        limit: int = 50,
    ) -> list[CharitySearchResult]:
        """Search for charities across jurisdictions.

        Args:
            name: Name search term (partial match).
            postcode: Postcode to search within.
            country: Filter by country ("England", "Wales", "Scotland",
                "Northern Ireland").
            limit: Maximum results to return.

        Returns:
            List of CharitySearchResult objects.
        """
        del name, postcode, country, limit
        raise NotImplementedError(
            "Find That Charity v1 no longer exposes the legacy filtered charity search endpoint"
        )

    def _parse_charity_detail(
        self,
        payload: dict[str, Any],
        *,
        requested_id: str,
    ) -> CharityDetail:
        """Parse the current v1 charity response while keeping stable Soundings fields."""
        active = payload.get("active")
        return CharityDetail(
            id=requested_id,
            name=str(payload.get("name") or ""),
            registered_date=payload.get("date_registered"),
            postcode=payload.get("postcode"),
            country=_country_from_id(requested_id),
            status="Registered" if active is True else "Removed" if active is False else "",
            activities=payload.get("activities"),
            charitable_objects=None,
            source_url=f"https://findthatcharity.uk/charity/{requested_id}",
        )


def _country_from_id(charity_id: str) -> str:
    value = charity_id.upper()
    if value.startswith("SC") or value.startswith("GB-SC-"):
        return "Scotland"
    if value.startswith("NI") or value.startswith("GB-NIC-"):
        return "Northern Ireland"
    return "England/Wales"
