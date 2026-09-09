"""GiveFoodAdapter — food-bank counts and locations from Give Food.

Fetches Give Food's daily food-bank dump once (cached 24h), then matches each
location to a place by point-in-polygon on its coordinates. The LSOA GSS code
on each row is a fallback when coordinates are missing.

Food banks are volunteered/maintained data; every returned value carries the
methodology caveat below.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.givefood.client import GiveFoodClient
from soundings.adapters.passthrough_base import PassthroughAdapter
from soundings.contracts.indicator_value import IndicatorValue

SOURCE_ID = "givefood"
FOOD_BANKS_INDICATOR = "infrastructure.food_banks_count"
METHODOLOGY_CAVEAT = (
    "Food bank locations from Give Food (givefood.org.uk), updated daily. "
    "Counts distribution locations whose coordinates fall within the place boundary."
)

_log = logging.getLogger(__name__)


class GiveFoodAdapter(PassthroughAdapter):
    source_id = SOURCE_ID

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        ttl: timedelta = timedelta(hours=24),
        client: GiveFoodClient | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(engine, ttl=ttl, rate_per_second=1.0, http_client=http_client)
        self._gf = client or GiveFoodClient(http_client=http_client)

    async def _call_upstream(self, client: httpx.AsyncClient, cache_key: str) -> Any | None:
        del client, cache_key
        raise NotImplementedError("GiveFoodAdapter routes via fetch_indicator override")

    async def _cached_dump(self) -> list[dict[str, Any]]:
        cached = await self._cache.get(self.source_id, "foodbanks:all")
        if isinstance(cached, list):
            return cached
        rows = await self._gf.fetch_foodbanks()
        await self._cache.put(self.source_id, "foodbanks:all", rows, ttl=self._ttl)
        return rows

    async def _locations_within(self, place_id: str) -> list[dict[str, Any]]:
        """Dump rows whose coordinates fall inside the place polygon.

        Coordinate-bearing rows are matched by ST_Within; rows lacking
        coordinates but carrying an LSOA code fall back to current hierarchy
        membership.
        """
        rows = await self._cached_dump()
        coord_rows = [r for r in rows if r["lat"] is not None and r["lng"] is not None]
        nocoord_rows = [r for r in rows if (r["lat"] is None or r["lng"] is None) and r.get("lsoa")]
        matched: list[dict[str, Any]] = []

        if coord_rows:
            lngs = [r["lng"] for r in coord_rows]
            lats = [r["lat"] for r in coord_rows]
            async with self._engine.connect() as conn:
                res = (
                    await conn.execute(
                        text(
                            """
                            SELECT u.idx
                            FROM unnest(:lngs ::float8[], :lats ::float8[])
                                WITH ORDINALITY AS u(lng, lat, idx)
                            JOIN geography.place g ON g.id = :pid
                            WHERE g.geom IS NOT NULL
                              AND ST_Within(ST_SetSRID(ST_Point(u.lng, u.lat), 4326), g.geom)
                            """
                        ),
                        {"lngs": lngs, "lats": lats, "pid": place_id},
                    )
                ).all()
            matched.extend(coord_rows[r.idx - 1] for r in res)

        if nocoord_rows:
            lsoa_ids = ["lsoa21:" + r["lsoa"] for r in nocoord_rows]
            async with self._engine.connect() as conn:
                res = (
                    await conn.execute(
                        text(
                            """
                            SELECT h.child_id AS id
                            FROM geography.current_place_hierarchy h
                            WHERE h.parent_id = :pid AND h.child_id = ANY(:ids)
                            UNION
                            SELECT g.id AS id FROM geography.place g
                            WHERE g.id = :pid AND g.id = ANY(:ids)
                            """
                        ),
                        {"pid": place_id, "ids": lsoa_ids},
                    )
                ).all()
            within = {r.id for r in res}
            matched.extend(r for r in nocoord_rows if "lsoa21:" + r["lsoa"] in within)

        return matched

    async def fetch_indicator(
        self, indicator_key: str, place_id: str, period: str | None
    ) -> IndicatorValue | None:
        if indicator_key != FOOD_BANKS_INDICATOR:
            return None

        cache_key = f"count:{place_id}"
        cached = await self._cache.get(self.source_id, cache_key)
        if isinstance(cached, dict):
            count = int(cached.get("count", 0))
            period_used = str(cached.get("period", ""))
        else:
            within = await self._locations_within(place_id)
            count = len(within)
            period_used = period or datetime.now(tz=UTC).strftime("%Y-%m")
            await self._cache.put(
                self.source_id,
                cache_key,
                {"count": count, "period": period_used},
                ttl=self._ttl,
            )

        source_ref = await self._build_source_ref(
            retrieved_at=datetime.now(tz=UTC), cache_status="cached"
        )
        return IndicatorValue(
            place_id=place_id,
            indicator=indicator_key,
            value=float(count),
            unit="count",
            period=period_used,
            source=source_ref,
            caveats=[METHODOLOGY_CAVEAT],
            confidence="official",
        )

    async def amenity_locations(self, indicator_key: str, place_id: str) -> dict[str, Any] | None:
        """GeoJSON FeatureCollection of food-bank locations within a place."""
        if indicator_key != FOOD_BANKS_INDICATOR:
            return None

        cache_key = f"geo:{place_id}"
        cached = await self._cache.get(self.source_id, cache_key)
        if isinstance(cached, dict):
            return cached

        within = await self._locations_within(place_id)
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["lng"], r["lat"]]},
                "properties": {"name": r["name"], "layer": indicator_key},
            }
            for r in within
            if r["lat"] is not None and r["lng"] is not None
        ]
        fc = {"type": "FeatureCollection", "features": features}
        await self._cache.put(self.source_id, cache_key, fc, ttl=self._ttl)
        return fc

    async def pre_warm_for_places(self, place_ids: list[str]) -> None:
        """Warm the dump once, then per-place counts. Driven by the pre_warmer
        daemon on the source's daily cadence so user reads stay warm."""
        await self._cached_dump()
        for place_id in place_ids:
            try:
                await self.fetch_indicator(FOOD_BANKS_INDICATOR, place_id, None)
            except Exception:
                _log.exception("givefood pre_warm failed for place_id=%s", place_id)
