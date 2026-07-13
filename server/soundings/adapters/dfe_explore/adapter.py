"""DfeExploreAdapter — passthrough over the DfE EES dataset query API.

For each indicator, the adapter queries the dataset once per place
— pulling all available time periods for that location — and caches
the result. `fetch_indicator` picks the latest period (or an explicit
one); `fetch_trend` slices to a window.

Response shape (DfE EES v1):
    {
      "paging": {...},
      "results": [
        {
          "timePeriod": {"code": "AY", "period": "2022/2023"},
          "locations": {"LA": "loc-stockton", "NAT": "loc-england"},
          "filters": {"<filter_id>": "<option_id>"},
          "values": {"<indicator_id>": "0.215"}
        }
      ]
    }
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.dfe_explore.client import DfeExploreClient
from soundings.adapters.dfe_explore.mapping import DfeMapping, load_dfe_mapping
from soundings.adapters.passthrough_base import PassthroughAdapter
from soundings.contracts.indicator_value import IndicatorValue
from soundings.contracts.trend import Trend, TrendPoint

SOURCE_ID = "dfe.explore"


class DfeExploreAdapter(PassthroughAdapter):
    source_id = SOURCE_ID

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        ttl: timedelta = timedelta(hours=24),
        dfe_client: DfeExploreClient | None = None,
        mapping_path: Path | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(engine, ttl=ttl, http_client=http_client)
        self._dfe = dfe_client or DfeExploreClient(http_client=http_client)
        self._mapping = {m.indicator_key: m for m in load_dfe_mapping(mapping_path)}

    async def fetch_indicator(
        self,
        indicator_key: str,
        place_id: str,
        period: str | None,
    ) -> IndicatorValue | None:
        mapping = self._mapping.get(indicator_key)
        if mapping is None:
            return None
        points = await self._fetch_points(mapping, place_id)
        if not points:
            return None
        if period is not None:
            matched = [p for p in points if p["period"] == period]
            if not matched:
                return None
            chosen = matched[0]
        else:
            chosen = max(points, key=lambda p: p["period"])
        source_ref = await self._build_source_ref(
            retrieved_at=datetime.now(tz=UTC), cache_status="cached"
        )
        return IndicatorValue(
            place_id=place_id,
            indicator=indicator_key,
            value=chosen["value"],
            unit=mapping.unit,
            period=chosen["period"],
            source=source_ref,
            caveats=mapping.caveats,
            confidence="official",
        )

    async def fetch_trend(
        self,
        indicator_key: str,
        place_id: str,
        period_from: str | None = None,
        period_to: str | None = None,
    ) -> Trend | None:
        mapping = self._mapping.get(indicator_key)
        if mapping is None:
            return None
        points = await self._fetch_points(mapping, place_id)
        in_window = [
            TrendPoint(period=p["period"], value=p["value"])
            for p in sorted(points, key=lambda r: r["period"])
            if _within_window(p["period"], period_from, period_to)
        ]
        if not in_window:
            return None
        source_ref = await self._build_source_ref(
            retrieved_at=datetime.now(tz=UTC), cache_status="cached"
        )
        return Trend(
            place_id=place_id,
            indicator=indicator_key,
            unit=mapping.unit,
            points=in_window,
            source=source_ref,
        )

    async def _fetch_points(self, mapping: DfeMapping, place_id: str) -> list[dict[str, Any]]:
        place_code = _strip_type_prefix(place_id)
        cache_key = f"dfe:{mapping.data_set_id}:{mapping.indicator_id}:{place_code}"
        cached = await self._cache.get(self.source_id, cache_key)
        if cached is not None and isinstance(cached, list):
            return cached

        criteria: dict[str, Any] = {
            "locations": {
                "in": [{"level": mapping.location_level, "code": place_code}],
            },
        }
        if mapping.filters:
            criteria["filters"] = mapping.filters

        payload = await self._dfe.query_dataset(
            data_set_id=mapping.data_set_id,
            indicators=[mapping.indicator_id],
            criteria=criteria,
        )
        points = _materialise_points(payload, mapping)
        if points:
            await self._cache.put(self.source_id, cache_key, points, ttl=self._ttl)
        return points

    async def _call_upstream(self, client: httpx.AsyncClient, cache_key: str) -> Any:
        del client, cache_key
        raise NotImplementedError("DfeExploreAdapter routes via fetch_indicator override")


def _strip_type_prefix(place_id: str) -> str:
    if ":" in place_id:
        return place_id.split(":", 1)[1]
    return place_id


def _materialise_points(payload: dict[str, Any], mapping: DfeMapping) -> list[dict[str, Any]]:
    """Flatten DfE result rows into {period, value} dicts.

    The DfE POST query API's `criteria.filters` doesn't narrow results
    (returns 0 rows when filter conditions are specified). Instead we
    query with location only and post-filter the result rows to match
    `mapping.filter_selection` (a dict of filter_id → option_id).

    EES values arrive as strings — coerce to float. Multiple rows for
    one (place, period) shouldn't happen after filter_selection, but
    if it does we take the last one seen (preserves DfE's ordering).
    """
    results = payload.get("results") or []
    out: list[dict[str, Any]] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        # Post-filter: only keep rows matching all filter_selection criteria
        filters = row.get("filters") or {}
        if mapping.filter_selection:
            if not all(filters.get(k) == v for k, v in mapping.filter_selection.items()):
                continue
        period_obj = row.get("timePeriod") or {}
        period = period_obj.get("period") if isinstance(period_obj, dict) else None
        if not period:
            continue
        values = row.get("values") or {}
        raw = values.get(mapping.indicator_id) if isinstance(values, dict) else None
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        out.append({"period": str(period), "value": value})
    return out


def _within_window(period: str, frm: str | None, to: str | None) -> bool:
    if frm is not None and period < frm:
        return False
    if to is not None and period > to:
        return False
    return True
