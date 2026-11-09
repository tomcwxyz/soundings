"""Idempotent import of official WIMD 2025 national LSOA ranks/deciles.

Run after catalogue and geography are seeded:
  python -m soundings.loader.run --once gov_wales.wimd2025

Not used for England, Scotland, or UK-wide comparisons. A missing geography
spine is reported explicitly; it is never treated as a successful zero load.
"""

import json
from datetime import UTC, datetime

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.base import LoaderAdapter, LoaderResult
from soundings.adapters.gov_wales_wimd2025.parser import WimdRow, parse_wimd_csv

SOURCE_ID = "gov_wales.wimd2025"
DATASET_ID = "9706edd9-73ad-4902-bb12-7ccd7038626e"
BULK_URL = f"https://api.stats.gov.wales/v1/{DATASET_ID}/download/csv?lang=en-gb"
PERIOD = "2025"
LSOA_COUNT = 1917
BATCH_SIZE = 1000
CAVEATS = [
    "WIMD 2025 is relative deprivation within Wales, not a count of deprived people.",
    "Rank 1 and decile 1 are most deprived. Rankings are not magnitudes or time trends.",
    "WIMD 2025 cannot be directly compared with English IMD or Scottish SIMD.",
]
UPSERT = text(
    "INSERT INTO data.indicator_value "
    "(place_id, indicator_key, period, value, source_id, retrieved_at, caveats) "
    "VALUES (:place_id, :indicator_key, :period, :value, :source_id, "
    "        :retrieved_at, CAST(:caveats AS jsonb)) "
    "ON CONFLICT (place_id, indicator_key, period) "
    "DO UPDATE SET value = EXCLUDED.value, source_id = EXCLUDED.source_id, "
    "              retrieved_at = EXCLUDED.retrieved_at, caveats = EXCLUDED.caveats"
)


class Wimd2025Loader(LoaderAdapter):
    source_id = SOURCE_ID

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        http_client: httpx.AsyncClient | None = None,
        url: str = BULK_URL,
        minimum_lsoas: int = LSOA_COUNT,
    ) -> None:
        super().__init__(engine)
        self._client = http_client
        self._url = url
        self._minimum_lsoas = minimum_lsoas

    async def load(self, run_id: str | None = None) -> LoaderResult:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=120.0, follow_redirects=True)
        try:
            response = await client.get(self._url)
            response.raise_for_status()
            return await self.load_from_bytes(response.content)
        finally:
            if owns_client:
                await client.aclose()

    async def load_from_bytes(self, content: bytes) -> LoaderResult:
        parsed = parse_wimd_csv(content)
        overall_rank_codes = {
            row.lsoa_code for row in parsed if row.indicator_key == "deprivation.wimd2025.rank"
        }
        overall_decile_codes = {
            row.lsoa_code for row in parsed if row.indicator_key == "deprivation.wimd2025.decile"
        }
        if (
            len(overall_rank_codes) < self._minimum_lsoas
            or len(overall_decile_codes) < self._minimum_lsoas
        ):
            raise ValueError(
                "Incomplete WIMD 2025 CSV: "
                f"{len(overall_rank_codes)} ranks, {len(overall_decile_codes)} deciles; "
                f"expected at least {self._minimum_lsoas} Welsh LSOAs"
            )

        async with self._engine.connect() as conn:
            existing = await conn.execute(
                text("SELECT id FROM geography.place WHERE type = 'lsoa21' AND code LIKE 'W%'")
            )
            known_places = {r.id for r in existing.all()}
        if not known_places:
            raise RuntimeError(
                "No Welsh LSOA 2021 places in geography.place; seed geography before WIMD"
            )

        retrieved_at = datetime.now(tz=UTC)
        caveats_json = json.dumps(CAVEATS)
        records = [
            self._to_record(row, retrieved_at, caveats_json)
            for row in parsed
            if f"lsoa21:{row.lsoa_code}" in known_places
        ]
        if not records:
            raise RuntimeError("WIMD 2025 LSOAs do not match Soundings geography")
        async with self._engine.begin() as conn:
            for start in range(0, len(records), BATCH_SIZE):
                await conn.execute(UPSERT, records[start : start + BATCH_SIZE])

        matched = len({r["place_id"] for r in records})
        missing = len(overall_rank_codes) - matched
        return LoaderResult(
            rows_written=len(records),
            notes=f"{matched} Welsh LSOAs matched; {missing} source LSOAs not in geography",
        )

    @staticmethod
    def _to_record(row: WimdRow, retrieved_at: datetime, caveats_json: str) -> dict[str, object]:
        return {
            "place_id": f"lsoa21:{row.lsoa_code}",
            "indicator_key": row.indicator_key,
            "period": PERIOD,
            "value": row.value,
            "source_id": SOURCE_ID,
            "retrieved_at": retrieved_at,
            "caveats": caveats_json,
        }
