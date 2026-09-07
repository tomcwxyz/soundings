"""Async HTTP wrapper for the official 360Giving API.

Base: https://api.threesixtygiving.org/api/v1

The API is intentionally organisation-centric. Soundings uses it for targeted
organisation/funder hydration; broad grant search is served from the local
``data.grant_record`` index.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
from aiolimiter import AsyncLimiter

THREESIXTYGIVING_BASE = "https://api.threesixtygiving.org/api/v1"
DEFAULT_PAGE_SIZE = 1000  # documented maximum; fewer requests is kinder to the upstream API
DEFAULT_RATE_PER_SECOND = 2.0  # documented per-IP limit


@dataclass
class OrgAggregate:
    org_id: str
    grants: int
    total_gbp: float
    earliest_grant_date: str | None
    latest_grant_date: str | None


class ThreeSixtyGivingClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        *,
        rate_per_second: float = DEFAULT_RATE_PER_SECOND,
    ) -> None:
        self._client = http_client
        self._owns_client = http_client is None
        self._limiter = AsyncLimiter(max_rate=rate_per_second, time_period=1)

    async def _get(self, url: str) -> httpx.Response:
        """GET JSON with shared rate limiting and conservative 429 retry."""
        client = self._client or httpx.AsyncClient(timeout=30.0)
        try:
            for attempt in range(4):
                async with self._limiter:
                    response = await client.get(url, headers={"Accept": "application/json"})
                if response.status_code != 429:
                    return response
                if attempt == 3:
                    return response
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = max(float(retry_after), 0.5) if retry_after else 0.6
                except ValueError:
                    delay = 0.6
                await asyncio.sleep(delay)
            raise RuntimeError("unreachable")
        finally:
            if self._owns_client:
                await client.aclose()

    async def get_org_aggregate(self, org_id: str) -> OrgAggregate | None:
        """Return lifetime recipient stats, or ``None`` if not present."""
        response = await self._get(f"{THREESIXTYGIVING_BASE}/org/{org_id}/")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        recipient = payload.get("recipient") if isinstance(payload, dict) else None
        if not recipient:
            return None
        aggregate = recipient.get("aggregate") or {}
        currencies = aggregate.get("currencies") or {}
        gbp = currencies.get("GBP") or {}
        return OrgAggregate(
            org_id=str(payload.get("org_id", org_id)),
            grants=int(aggregate.get("grants") or 0),
            total_gbp=float(gbp.get("total") or 0.0),
            earliest_grant_date=aggregate.get("earliest_grant_date"),
            latest_grant_date=aggregate.get("latest_grant_date"),
        )

    async def iter_grants_received(
        self,
        org_id: str,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield every indexed grant where ``org_id`` is the recipient."""
        async for grant in self._iter_grants(org_id, "grants_received", page_size=page_size):
            yield grant

    async def iter_grants_made(
        self,
        org_id: str,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield every indexed grant where ``org_id`` is the funder."""
        async for grant in self._iter_grants(org_id, "grants_made", page_size=page_size):
            yield grant

    async def _iter_grants(
        self,
        org_id: str,
        endpoint: str,
        *,
        page_size: int,
    ) -> AsyncIterator[dict[str, Any]]:
        page_size = max(1, min(page_size, DEFAULT_PAGE_SIZE))
        url: str | None = (
            f"{THREESIXTYGIVING_BASE}/org/{org_id}/{endpoint}/?limit={page_size}"
        )
        while url is not None:
            response = await self._get(url)
            if response.status_code == 404:
                return
            response.raise_for_status()
            payload = response.json()
            for grant in payload.get("results") or []:
                yield grant
            next_url = payload.get("next")
            url = str(next_url) if next_url else None
