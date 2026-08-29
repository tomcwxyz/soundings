"""DwpStatXploreAdapter — passthrough over the Stat-Xplore cube API.

Single-value indicator requests are deliberately narrow: the adapter resolves
the latest available period from Stat-Xplore's authenticated schema, then asks
the table endpoint for just that local authority + month. This avoids making
ordinary Ask requests wait for the entire UC time series.

Trend requests still fetch a series, but requested windows are recoded to only
the periods needed. Results and schema-derived date IDs are cached for 24h by
default.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.dwp_statxplore.client import StatXploreClient
from soundings.adapters.dwp_statxplore.mapping import (
    StatXploreMapping,
    load_statxplore_mapping,
)
from soundings.adapters.passthrough_base import PassthroughAdapter
from soundings.contracts.indicator_value import IndicatorValue
from soundings.contracts.trend import Trend, TrendPoint

SOURCE_ID = "dwp.statxplore"


class DwpStatXploreAdapter(PassthroughAdapter):
    source_id = SOURCE_ID

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        ttl: timedelta = timedelta(hours=24),
        statxplore_client: StatXploreClient | None = None,
        mapping_path: Path | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(engine, ttl=ttl, http_client=http_client)
        self._statxplore = statxplore_client or StatXploreClient(http_client=http_client)
        self._mapping = {m.indicator_key: m for m in load_statxplore_mapping(mapping_path)}

    async def fetch_indicator(
        self,
        indicator_key: str,
        place_id: str,
        period: str | None,
    ) -> IndicatorValue | None:
        mapping = self._mapping.get(indicator_key)
        if mapping is None:
            return None

        date_values = await self._date_value_ids(mapping)
        target_period = period or (max(date_values) if date_values else None)
        if target_period is None or target_period not in date_values:
            return None

        points = await self._fetch_points(
            mapping,
            place_id,
            periods=[target_period],
            date_values=date_values,
        )
        if not points:
            return None
        chosen = points[0]

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

        periods: list[str] | None = None
        date_values: dict[str, str] | None = None
        if period_from is not None or period_to is not None:
            date_values = await self._date_value_ids(mapping)
            periods = [p for p in sorted(date_values) if _within_window(p, period_from, period_to)]
            if not periods:
                return None

        points = await self._fetch_points(
            mapping,
            place_id,
            periods=periods,
            date_values=date_values,
        )
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

    async def _date_value_ids(self, mapping: StatXploreMapping) -> dict[str, str]:
        cache_key = f"statxplore:schema-dates:{mapping.database}:{mapping.date_dim}"
        cached = await self._cache.get(self.source_id, cache_key)
        if isinstance(cached, dict) and all(
            isinstance(k, str) and isinstance(v, str) for k, v in cached.items()
        ):
            return cached

        field = await self._statxplore.get_schema(mapping.date_dim)
        valueset_id: str | None = None
        for child in field.get("children", []):
            if not isinstance(child, dict):
                continue
            child_id = child.get("id")
            if child.get("type") == "VALUESET" and isinstance(child_id, str):
                valueset_id = child_id
                break
        if valueset_id is None:
            return {}

        valueset = await self._statxplore.get_schema(valueset_id)
        out: dict[str, str] = {}
        for child in valueset.get("children", []):
            if not isinstance(child, dict):
                continue
            value_id = child.get("id")
            if not isinstance(value_id, str):
                continue
            period = value_id.rsplit(":", 1)[-1]
            if period:
                out[period] = value_id

        if out:
            await self._cache.put(self.source_id, cache_key, out, ttl=self._ttl)
        return out

    async def _fetch_points(
        self,
        mapping: StatXploreMapping,
        place_id: str,
        *,
        periods: list[str] | None = None,
        date_values: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        place_code = _strip_type_prefix(place_id)
        period_key = ",".join(periods) if periods else "all"
        cache_key = f"statxplore:{mapping.database}:{mapping.measures[0]}:{place_code}:{period_key}"
        cached = await self._cache.get(self.source_id, cache_key)
        if cached is not None and isinstance(cached, list):
            return cached

        recodes: dict[str, Any] = {
            mapping.geography_dim: {
                "map": [[mapping.geography_value_template.format(place_code=place_code)]],
                "total": False,
            }
        }

        if periods:
            date_values = date_values or await self._date_value_ids(mapping)
            selected = [date_values[p] for p in periods if p in date_values]
            if not selected:
                return []
            recodes[mapping.date_dim] = {
                "map": [[value_id] for value_id in selected],
                "total": False,
            }

        payload = await self._statxplore.get_table(
            database=mapping.database,
            measures=mapping.measures,
            dimensions=[[mapping.geography_dim], [mapping.date_dim]],
            recodes=recodes,
        )
        points = _materialise_points(payload, mapping)
        if points:
            await self._cache.put(self.source_id, cache_key, points, ttl=self._ttl)
        return points

    async def _call_upstream(self, client: httpx.AsyncClient, cache_key: str) -> Any:
        del client, cache_key
        raise NotImplementedError("DwpStatXploreAdapter routes via fetch_indicator override")


def _strip_type_prefix(place_id: str) -> str:
    if ":" in place_id:
        return place_id.split(":", 1)[1]
    return place_id


def _materialise_points(
    payload: dict[str, Any], mapping: StatXploreMapping
) -> list[dict[str, Any]]:
    measure_id = mapping.measures[0]
    cube = (payload.get("cubes") or {}).get(measure_id) or {}
    values = cube.get("values") or []
    if not values:
        return []
    inner = values[0] if isinstance(values[0], list) else values
    fields = payload.get("fields") or []
    if len(fields) < 2:
        return []
    date_items = (fields[1].get("items") or []) if isinstance(fields[1], dict) else []

    out: list[dict[str, Any]] = []
    for idx, item in enumerate(date_items):
        if idx >= len(inner):
            break
        labels = item.get("labels") or []
        if not labels:
            continue
        period = str(labels[0])
        raw = inner[idx]
        value = float(raw) if isinstance(raw, (int, float)) else None
        out.append({"period": period, "value": value})
    return out


def _within_window(period: str, frm: str | None, to: str | None) -> bool:
    if frm is not None and period < frm:
        return False
    if to is not None and period > to:
        return False
    return True
