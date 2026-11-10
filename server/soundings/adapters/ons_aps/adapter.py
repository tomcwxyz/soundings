"""OnsApsAdapter — passthrough over Nomis APS datasets.

APS is delivered via the same Nomis Open Data API used by Census and MYE,
so this adapter reuses `NomisClient`. The new bit is mode: passthrough +
quarterly time-series support. One upstream call per (dataset_id,
measures, place_code) returns up to 20 quarterly periods (`time=
latestMINUS19-latest`); both fetch_indicator and fetch_trend serve from
that cached series.

Mapping is loaded from `catalogue/nomis-mapping.yaml`, the same file
used by the MYE and Census loaders, filtered to `source_id == "ons.aps"`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.nomis.client import NomisClient
from soundings.adapters.nomis.mapping import NomisMapping, load_nomis_mapping
from soundings.adapters.passthrough_base import PassthroughAdapter
from soundings.catalogue.contracts import get_contract_registry
from soundings.contracts.indicator_value import IndicatorValue
from soundings.contracts.trend import Trend, TrendPoint

SOURCE_ID = "ons.aps"
DEFAULT_MAPPING_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent.parent / "catalogue" / "nomis-mapping.yaml"
)
# APS quarterly cadence over 5 years ≈ 20 periods. Nomis exposes this as
# a relative range against the latest observation.
TIME_RANGE = "latestMINUS19-latest"
# Only used for an indicator the catalogue does not know; every catalogued
# indicator is served with its catalogue unit.
FALLBACK_UNIT = "value"


def _unit_for(indicator_key: str) -> str:
    return get_contract_registry().unit(indicator_key) or FALLBACK_UNIT


def _cache_key(mapping: NomisMapping, place_code: str) -> str:
    """One cache entry per distinct upstream query.

    Several indicators share a dataset and measure and differ only in a
    dimension filter (employment is variable 45, unemployment variable 84),
    so every extra parameter is part of the key.
    """
    dimensions = ",".join(f"{k}={v}" for k, v in sorted(mapping.extra_params.items()))
    return f"aps:{mapping.dataset_id}:{mapping.measures or 'na'}:{dimensions or 'na'}:{place_code}"


class OnsApsAdapter(PassthroughAdapter):
    source_id = SOURCE_ID

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        ttl: timedelta = timedelta(hours=24),
        nomis_client: NomisClient | None = None,
        mapping_path: Path | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(engine, ttl=ttl, http_client=http_client)
        self._nomis = nomis_client or NomisClient()
        mappings = load_nomis_mapping(mapping_path or DEFAULT_MAPPING_PATH)
        self._mapping = {m.indicator_key: m for m in mappings if m.source_id == SOURCE_ID}

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
            unit=_unit_for(indicator_key),
            period=chosen["period"],
            source=source_ref,
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
            unit=_unit_for(indicator_key),
            points=in_window,
            source=source_ref,
        )

    async def _fetch_points(self, mapping: NomisMapping, place_id: str) -> list[dict[str, Any]]:
        place_type, _, place_code = place_id.partition(":")
        if place_type not in mapping.geography_type_codes:
            return []

        cache_key = _cache_key(mapping, place_code)
        cached = await self._cache.get(self.source_id, cache_key)
        if cached is not None and isinstance(cached, list):
            return cached

        payload = await self._nomis.get_observations(
            dataset_id=mapping.dataset_id,
            geography=place_code,
            measures=mapping.measures,
            time=TIME_RANGE,
            **mapping.extra_params,
        )
        points = _materialise_points(payload, mapping)
        if points:
            await self._cache.put(self.source_id, cache_key, points, ttl=self._ttl)
        return points

    async def _call_upstream(self, client: httpx.AsyncClient, cache_key: str) -> Any:
        del client, cache_key
        raise NotImplementedError("OnsApsAdapter routes via fetch_indicator override")


def _materialise_points(payload: dict[str, Any], mapping: NomisMapping) -> list[dict[str, Any]]:
    """Flatten Nomis `obs` rows into `{period, value}` dicts.

    Applies `value_scale` so percent-shaped measures (0–100) collapse to
    fractions (0–1) when the indicator contract calls for it.
    """
    out: list[dict[str, Any]] = []
    for obs in payload.get("obs", []) or []:
        if not isinstance(obs, dict):
            continue
        time_obj = obs.get("time") or {}
        period = time_obj.get("description") if isinstance(time_obj, dict) else None
        value_obj = obs.get("obs_value") or {}
        raw = value_obj.get("value") if isinstance(value_obj, dict) else None
        if not period or raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if mapping.value_scale is not None:
            value *= mapping.value_scale
        out.append({"period": str(period), "value": value})
    return out


def _within_window(period: str, frm: str | None, to: str | None) -> bool:
    if frm is not None and period < frm:
        return False
    if to is not None and period > to:
        return False
    return True
