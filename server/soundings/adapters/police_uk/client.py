"""Async HTTP wrapper for the data.police.uk public API.

Base: https://data.police.uk/api

Unauthenticated. The endpoint
    GET /crimes-street/{category}?lat=…&lng=…&date=YYYY-MM
returns the JSON array of crimes within ~1 mile of the supplied point
for the given month. The geographic semantics are a fixed-radius
circle, not an LTLA polygon — `PoliceUkAdapter` carries the
methodology caveat that flows from this.

`date` is optional; when omitted, police.uk returns the latest
available month. The API responds with 404 for points that fall
outside any force boundary; we treat that as an empty result, not a
fatal error.
"""

import asyncio
from typing import Any

import httpx
from aiolimiter import AsyncLimiter

POLICE_UK_BASE = "https://data.police.uk/api"


class PoliceUkClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        *,
        rate_per_second: float = 3.0,
    ) -> None:
        self._client = http_client
        self._owns_client = http_client is None
        self._limiter = AsyncLimiter(max_rate=rate_per_second, time_period=1)

    async def get_crimes(
        self,
        *,
        category: str,
        lat: float,
        lng: float,
        date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all crimes of `category` within ~1 mile of (lat, lng) for `date`."""
        params: dict[str, str] = {"lat": str(lat), "lng": str(lng)}
        if date is not None:
            params["date"] = date

        client = self._client or httpx.AsyncClient(timeout=60.0)
        try:
            response: httpx.Response | None = None
            for attempt in range(4):
                async with self._limiter:
                    response = await client.get(
                        f"{POLICE_UK_BASE}/crimes-street/{category}",
                        params=params,
                    )
                if response.status_code == 404:
                    return []
                if response.status_code == 429 and attempt < 3:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        delay = float(retry_after) if retry_after is not None else 0.0
                    except ValueError:
                        delay = 0.0
                    if delay <= 0:
                        delay = float(2**attempt)
                    await asyncio.sleep(min(delay, 8.0))
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    return []
                return payload
            return []
        finally:
            if self._owns_client:
                await client.aclose()

    async def get_last_updated(self) -> str:
        """Return the latest YYYY-MM with published crime data.

        Police.uk responds with `{"date": "YYYY-MM-DD"}`; we strip to
        the month component to align with the `date=` query parameter
        on `/crimes-street`.
        """
        async with self._limiter:
            client = self._client or httpx.AsyncClient(timeout=30.0)
            try:
                response = await client.get(f"{POLICE_UK_BASE}/crime-last-updated")
                response.raise_for_status()
                payload = response.json()
            finally:
                if self._owns_client:
                    await client.aclose()
        raw = payload.get("date") if isinstance(payload, dict) else None
        if not isinstance(raw, str) or len(raw) < 7:
            raise RuntimeError("police.uk /crime-last-updated returned no usable date")
        return raw[:7]
