"""Async HTTP wrapper for the DWP Stat-Xplore API.

Base: https://stat-xplore.dwp.gov.uk/webapi/rest/v1/

Requires `STATXPLORE_API_KEY` in the `APIKey` header.

Stat-Xplore organises data as cubes. Table queries use the opaque IDs returned
by the authenticated `/schema` endpoint. Schema responses can be paginated;
`get_schema` follows those pages so callers can safely inspect complete value
sets such as monthly dates.
"""

import os
from typing import Any
from urllib.parse import quote

import httpx
from aiolimiter import AsyncLimiter

STATXPLORE_BASE = "https://stat-xplore.dwp.gov.uk/webapi/rest/v1"
DEFAULT_TIMEOUT_SECONDS = 180.0


class StatXploreClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        *,
        rate_per_second: float = 2.0,
        api_key: str | None = None,
    ) -> None:
        self._client = http_client
        self._owns_client = http_client is None
        self._limiter = AsyncLimiter(max_rate=rate_per_second, time_period=1)
        self._explicit_api_key = api_key

    def _api_key(self) -> str | None:
        return self._explicit_api_key or os.environ.get("STATXPLORE_API_KEY")

    def _headers(self) -> dict[str, str]:
        api_key = self._api_key()
        if not api_key:
            raise RuntimeError("STATXPLORE_API_KEY is not set — cannot query Stat-Xplore")
        return {
            "APIKey": api_key,
            "Accept": "application/json",
        }

    async def get_schema(self, schema_id: str) -> dict[str, Any]:
        """Return one schema object, following paginated child lists."""

        url: str | None = f"{STATXPLORE_BASE}/schema/{quote(schema_id, safe='')}"
        combined: dict[str, Any] | None = None
        children: list[dict[str, Any]] = []

        client = self._client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS)
        try:
            while url:
                async with self._limiter:
                    response = await client.get(url, headers=self._headers())
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    return {}

                if combined is None:
                    combined = dict(payload)
                page_children = payload.get("children")
                if isinstance(page_children, list):
                    children.extend(c for c in page_children if isinstance(c, dict))

                next_link = response.links.get("next", {}).get("url")
                url = str(next_link) if next_link else None
        finally:
            if self._owns_client:
                await client.aclose()

        if combined is None:
            return {}
        if children:
            combined["children"] = children
        return combined

    async def get_table(
        self,
        *,
        database: str,
        measures: list[str],
        dimensions: list[list[str]],
        recodes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST a cube query and return the parsed JSON response."""

        body: dict[str, Any] = {
            "database": database,
            "measures": measures,
            "dimensions": dimensions,
        }
        if recodes:
            body["recodes"] = recodes

        async with self._limiter:
            client = self._client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS)
            try:
                response = await client.post(
                    f"{STATXPLORE_BASE}/table",
                    json=body,
                    headers={
                        **self._headers(),
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                payload = response.json()
            finally:
                if self._owns_client:
                    await client.aclose()

        if not isinstance(payload, dict):
            return {}
        return payload
