"""Async HTTP wrapper for the DfE Explore Education Statistics API.

Base: https://api.education.gov.uk/statistics/v1

Unauthenticated. POST `/data-sets/{data_set_id}/query` with a JSON body
combining `criteria` (filters, locations, timePeriods, geographicLevels)
and `indicators` to return rows of values. Each dataset is independently
versioned; identifiers rotate on annual republication.

Response shape (paginated):
    {
      "paging": {"page": 1, "pageSize": 100, "totalResults": ..., "totalPages": ...},
      "results": [
        {
          "timePeriod": {"code": "AY", "period": "2022/2023"},
          "locations": {"NAT": "loc1", "LA": "loc2"},
          "filters": {"<filter_id>": "<option_id>"},
          "values": {"<indicator_id>": "1708016"}
        }
      ]
    }
"""

from typing import Any

import httpx
from aiolimiter import AsyncLimiter

DFE_EXPLORE_BASE = "https://api.education.gov.uk/statistics/v1"


class DfeExploreClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        *,
        rate_per_second: float = 4.0,
    ) -> None:
        self._client = http_client
        self._owns_client = http_client is None
        self._limiter = AsyncLimiter(max_rate=rate_per_second, time_period=1)

    async def query_dataset(
        self,
        *,
        data_set_id: str,
        indicators: list[str],
        criteria: dict[str, Any] | None = None,
        page: int = 1,
        page_size: int = 1000,
    ) -> dict[str, Any]:
        """POST a dataset query and return the parsed JSON response.

        `criteria` is the optional facet selector (filters/locations/
        timePeriods/geographicLevels). When omitted, the API returns an
        unfiltered paginated slice.
        """
        body: dict[str, Any] = {
            "indicators": indicators,
            "page": page,
            "pageSize": page_size,
        }
        if criteria:
            body["criteria"] = criteria

        async with self._limiter:
            client = self._client or httpx.AsyncClient(timeout=60.0)
            try:
                response = await client.post(
                    f"{DFE_EXPLORE_BASE}/data-sets/{data_set_id}/query",
                    json=body,
                    headers={
                        "Accept": "application/json",
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
