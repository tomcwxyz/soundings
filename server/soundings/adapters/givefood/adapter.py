"""Give Food distribution-site counts and coordinates by place.

A successful location snapshot is retained beyond its normal refresh interval.
When both upstream sources fail we may serve a bounded, clearly labelled stale
snapshot rather than pretending that a locality has zero food banks.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.givefood.client import GiveFoodClient, GiveFoodUnavailableError
from soundings.adapters.passthrough_base import PassthroughAdapter
from soundings.contracts.indicator_value import IndicatorValue

SOURCE_ID = "givefood"
FOOD_BANKS_INDICATOR = "infrastructure.food_banks_count"
METHODOLOGY_CAVEAT = (
    "Distribution-site locations from Give Food (givefood.org.uk). "
    "Counts mapped locations within the place boundary; volunteer-maintained "
    "coverage may be incomplete and is not a measure of capacity."
)
_SNAPSHOT_KEY = "locations:v2"
_SNAPSHOT_RETENTION = timedelta(days=8)
_MAX_STALE = timedelta(days=7)
_log = logging.getLogger(__name__)


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _stale_notice(fetched_at: datetime) -> str:
    return (
        "Give Food could not be refreshed. Showing the last successful "
        f"distribution-site dataset from {fetched_at:%d %B %Y at %H:%M UTC}; "
        "locations and counts may have changed."
    )


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

    async def _cached_dump(self) -> tuple[list[dict[str, Any]], datetime, bool]:
        """Rows, last successful fetch time, and whether refresh failed.

        Persist for eight days while attempting to refresh after 24 hours.
        Only serve a stale snapshot for at most seven days. Never cache failure
        responses, and never turn a failed fetch into a zero count.
        """
        now = datetime.now(tz=UTC)
        cached = await self._cache.get(self.source_id, _SNAPSHOT_KEY)
        rows: list[dict[str, Any]] | None = None
        fetched_at: datetime | None = None
        if isinstance(cached, dict):
            candidate_rows = cached.get("rows")
            if isinstance(candidate_rows, list) and all(
                isinstance(row, dict) for row in candidate_rows
            ) and candidate_rows:
                rows = candidate_rows
                fetched_at = _as_utc(cached.get("fetched_at"))

        if rows is not None and fetched_at is not None and now - fetched_at < self._ttl:
            return rows, fetched_at, False

        try:
            fresh_rows = await self._gf.fetch_foodbanks()
        except GiveFoodUnavailableError:
            if (
                rows is not None
                and fetched_at is not None
                and timedelta(0) <= now - fetched_at <= _MAX_STALE
            ):
                _log.warning(
                    "givefood stale snapshot used fetched_at=%s records=%d",
                    fetched_at.isoformat(),
                    len(rows),
                )
                return rows, fetched_at, True
            _log.exception("givefood no usable upstream data or recent snapshot")
            raise

        if not fresh_rows:
            # Fake clients are subject to the same validity check as live ones.
            raise GiveFoodUnavailableError("Give Food returned an empty location list")
        await self._cache.put(
            self.source_id,
            _SNAPSHOT_KEY,
            {"rows": fresh_rows, "fetched_at": now.isoformat()},
            ttl=_SNAPSHOT_RETENTION,
        )
        return fresh_rows, now, False

    async def _locations_within(
        self, place_id: str
    ) -> tuple[list[dict[str, Any]], datetime, bool]:
        """Match distribution sites by coordinates, using LSOA where possible."""
        rows, fetched_at, stale = await self._cached_dump()
        coord_rows = [r for r in rows if r["lat"] is not None and r["lng"] is not None]
        nocoord_rows = [
            r for r in rows if (r["lat"] is None or r["lng"] is None) and r.get("lsoa")
        ]
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

        return matched, fetched_at, stale

    async def fetch_indicator(
        self, indicator_key: str, place_id: str, period: str | None
    ) -> IndicatorValue | None:
        if indicator_key != FOOD_BANKS_INDICATOR:
            return None

        cache_key = f"count:v2:{place_id}"
        cached = await self._cache.get(self.source_id, cache_key)
        if isinstance(cached, dict) and _as_utc(cached.get("fetched_at")) is not None:
            count = int(cached["count"])
            period_used = str(cached["period"])
            fetched_at = _as_utc(cached["fetched_at"])
            assert fetched_at is not None
            stale = bool(cached.get("stale", False))
        else:
            within, fetched_at, stale = await self._locations_within(place_id)
            count = len(within)
            period_used = period or fetched_at.strftime("%Y-%m")
            await self._cache.put(
                self.source_id,
                cache_key,
                {
                    "count": count,
                    "period": period_used,
                    "fetched_at": fetched_at.isoformat(),
                    "stale": stale,
                },
                ttl=timedelta(hours=1) if stale else self._ttl,
            )

        source_ref = await self._build_source_ref(
            retrieved_at=fetched_at, cache_status="stale" if stale else "cached"
        )
        caveats = [METHODOLOGY_CAVEAT]
        if stale:
            caveats.append(_stale_notice(fetched_at))
        return IndicatorValue(
            place_id=place_id,
            indicator=indicator_key,
            value=float(count),
            unit="count",
            period=period_used,
            source=source_ref,
            caveats=caveats,
            confidence="experimental",
        )

    async def amenity_locations(self, indicator_key: str, place_id: str) -> dict[str, Any] | None:
        """GeoJSON points and their age. Missing coordinates are not invented."""
        if indicator_key != FOOD_BANKS_INDICATOR:
            return None

        cache_key = f"geo:v2:{place_id}"
        cached = await self._cache.get(self.source_id, cache_key)
        if isinstance(cached, dict):
            return cached

        within, fetched_at, stale = await self._locations_within(place_id)
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["lng"], r["lat"]]},
                "properties": {
                    "name": r["name"],
                    "layer": indicator_key,
                    "url": r.get("url"),
                    "data_as_of": fetched_at.isoformat(),
                    "data_stale": stale,
                },
            }
            for r in within
            if r["lat"] is not None and r["lng"] is not None
        ]
        fc: dict[str, Any] = {
            "type": "FeatureCollection",
            "features": features,
            "data_as_of": fetched_at.isoformat(),
            "cache_status": "stale" if stale else "cached",
        }
        if stale:
            fc["warnings"] = [_stale_notice(fetched_at)]
        await self._cache.put(
            self.source_id,
            cache_key,
            fc,
            ttl=timedelta(hours=1) if stale else self._ttl,
        )
        return fc

    async def pre_warm_for_places(self, place_ids: list[str]) -> None:
        """Warm the location snapshot and then each requested place."""
        await self._cached_dump()
        for place_id in place_ids:
            try:
                await self.fetch_indicator(FOOD_BANKS_INDICATOR, place_id, None)
            except Exception:
                _log.exception("givefood pre_warm failed for place_id=%s", place_id)
