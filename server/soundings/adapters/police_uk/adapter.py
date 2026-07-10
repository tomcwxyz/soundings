"""PoliceUkAdapter — centroid-proximate crime aggregation.

For each LTLA, take the geometric centroid of the place polygon and
hit `data.police.uk` for crimes within ~1 mile of that point for each
of the last 12 months. Sum the counts, divide by the latest available
`population.total` to produce a per-1,000-population rate.

This is not a polygon-bounded aggregation. Large or geographically
dispersed LTLAs (Cornwall, Highland) are undercounted by a significant
fraction. Every returned `IndicatorValue` carries the methodology
caveat below; the adapter test asserts the caveat verbatim so a
refactor removing it fails CI.

Indicator → upstream category mapping is small and stable enough to
live as a Python constant; police.uk doesn't rotate category slugs
the way DfE rotates dataset UUIDs.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.passthrough_base import PassthroughAdapter
from soundings.adapters.police_uk.client import PoliceUkClient
from soundings.contracts.indicator_value import IndicatorValue

SOURCE_ID = "police_uk"
UNIT = "per 1,000 population"
WINDOW_MONTHS = 12

METHODOLOGY_CAVEAT = (
    "Crime count is centroid-proximate (police.uk API returns crimes within "
    "~1 mile of the supplied lat/lng), not LTLA-boundary accurate; "
    "underestimates large or dispersed LTLAs."
)

INDICATOR_CATEGORIES: dict[str, str] = {
    "crime.recorded_crime_rate": "all-crime",
    "crime.violence_rate": "violence-and-sexual-offences",
    "crime.asb_rate": "anti-social-behaviour",
}


class PoliceUkAdapter(PassthroughAdapter):
    source_id = SOURCE_ID

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        ttl: timedelta = timedelta(hours=24),
        police_client: PoliceUkClient | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(engine, ttl=ttl, http_client=http_client)
        self._police = police_client or PoliceUkClient(http_client=http_client)

    async def fetch_indicator(
        self,
        indicator_key: str,
        place_id: str,
        period: str | None,
    ) -> IndicatorValue | None:
        category = INDICATOR_CATEGORIES.get(indicator_key)
        if category is None:
            return None

        cache_key = f"police:{category}:{place_id}:{period or 'latest'}"
        cached = await self._cache.get(self.source_id, cache_key)
        if cached is not None and isinstance(cached, dict):
            total_count = int(cached.get("total_count", 0))
            end_month = str(cached.get("end_month", ""))
        else:
            centroid = await self._get_centroid(place_id)
            if centroid is None:
                return None
            lat, lng = centroid
            end_month = period or await self._police.get_last_updated()
            months = _walk_back_months(end_month, WINDOW_MONTHS)
            # Fire all 12 monthly API calls concurrently — the client's
            # AsyncLimiter (10 req/s) naturally throttles, so 12 requests
            # take ~1.2s instead of 12 × response-time sequentially.
            crime_lists = await asyncio.gather(
                *(
                    self._police.get_crimes(category=category, lat=lat, lng=lng, date=month)
                    for month in months
                )
            )
            total_count = sum(len(crimes) for crimes in crime_lists)
            await self._cache.put(
                self.source_id,
                cache_key,
                {"total_count": total_count, "end_month": end_month},
                ttl=self._ttl,
            )

        population = await self._get_population(place_id)
        if population is None or population <= 0:
            return None

        rate = total_count / population * 1000.0
        source_ref = await self._build_source_ref(
            retrieved_at=datetime.now(tz=UTC), cache_status="cached"
        )
        return IndicatorValue(
            place_id=place_id,
            indicator=indicator_key,
            value=rate,
            unit=UNIT,
            period=end_month,
            source=source_ref,
            caveats=[METHODOLOGY_CAVEAT],
            confidence="official",
        )

    async def _get_centroid(self, place_id: str) -> tuple[float, float] | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT ST_Y(ST_Centroid(geom)) AS lat, "
                        "ST_X(ST_Centroid(geom)) AS lng "
                        "FROM geography.place "
                        "WHERE id = :pid AND geom IS NOT NULL"
                    ),
                    {"pid": place_id},
                )
            ).first()
        if row is None or row.lat is None or row.lng is None:
            return None
        return float(row.lat), float(row.lng)

    async def _get_population(self, place_id: str) -> float | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT value FROM data.indicator_value "
                        "WHERE place_id = :pid AND indicator_key = 'population.total' "
                        "ORDER BY period DESC LIMIT 1"
                    ),
                    {"pid": place_id},
                )
            ).first()
        if row is None or row.value is None:
            return None
        return float(row.value)

    async def _call_upstream(self, client: httpx.AsyncClient, cache_key: str) -> Any:
        del client, cache_key
        raise NotImplementedError("PoliceUkAdapter routes via fetch_indicator override")


def _walk_back_months(end_month: str, count: int) -> list[str]:
    """Return `count` YYYY-MM strings ending at `end_month`, oldest first."""
    year_str, month_str = end_month.split("-")[:2]
    cursor = date(int(year_str), int(month_str), 1)
    months: list[str] = []
    for _ in range(count):
        months.append(cursor.strftime("%Y-%m"))
        if cursor.month == 1:
            cursor = date(cursor.year - 1, 12, 1)
        else:
            cursor = date(cursor.year, cursor.month - 1, 1)
    return list(reversed(months))
