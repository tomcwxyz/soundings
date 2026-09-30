"""Loader for the Priority Places for Food Index (PPFI) v2.1.

This first slice intentionally ingests the England LSOA deciles exposed by
HASP's official PPFI-IMD Explorer. It does not pretend that the mirror is the
full Great Britain dataset: the catalogue/caveats call out that Scotland and
Wales still need the primary HASP download wired in.

PPFI deciles are ranked within country and run in the opposite direction to
many "higher is worse" indicators: decile 1 is the highest-priority / most
food-vulnerable tenth of neighbourhoods; decile 10 is the lowest-priority
tenth.
"""

import json
import math
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.base import LoaderAdapter, LoaderResult
from soundings.adapters.ppfi.client import PpfiClient
from soundings.contracts.indicator_value import Confidence

SOURCE_ID = "hasp.ppfi"
PERIOD = "2024-07-14"
UPSERT_CHUNK = 2000

PPFI_COLUMNS: dict[str, str] = {
    "pp_dec_combined": "food.ppfi.overall_decile",
    "pp_dec_domain_supermarket_proximity": "food.ppfi.supermarket_proximity_decile",
    "pp_dec_domain_supermarket_accessibility": "food.ppfi.supermarket_accessibility_decile",
    "pp_dec_domain_ecommerce_access": "food.ppfi.ecommerce_access_decile",
    "pp_dec_domain_nonsupermarket_proximity": "food.ppfi.nonsupermarket_proximity_decile",
    "pp_dec_domain_socio_demographic": "food.ppfi.socioeconomic_barriers_decile",
    "pp_dec_domain_food_for_families": "food.ppfi.family_food_support_decile",
    "pp_dec_domain_fuel_poverty": "food.ppfi.fuel_poverty_decile",
}

CAVEATS = [
    "Priority Places for Food Index v2.1 is a static July 2024 composite index, "
    "not a current direct measure of food insecurity.",
    "Decile 1 is the highest-priority / most vulnerable tenth of neighbourhoods; "
    "decile 10 is the lowest-priority tenth.",
    "PPFI v2.1 covers Great Britain and ranks neighbourhoods within England, "
    "Scotland and Wales; country-relative ranks should not be compared directly "
    "across nations.",
    "This Soundings source spike currently loads the England LSOA slice from "
    "HASP's official PPFI-IMD Explorer mirror. Wider UK coverage is not yet "
    "loaded.",
]


class PpfiLoader(LoaderAdapter):
    source_id = SOURCE_ID
    default_confidence: Confidence = "modelled"

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        client: PpfiClient | None = None,
    ) -> None:
        super().__init__(engine)
        self._client = client or PpfiClient()

    async def load(self, run_id: str | None = None) -> LoaderResult:
        content = await self._client.fetch_geojson()
        values = list(self._extract(self._client.iter_rows(content)))
        written, skipped = await self._upsert_values(values)
        notes = (
            f"{skipped} values skipped (place not in spine or invalid decile)"
            if skipped
            else "England LSOA slice loaded from pinned HASP PPFI-IMD Explorer mirror"
        )
        return LoaderResult(rows_written=written, notes=notes)

    @staticmethod
    def _extract(rows: Iterable[dict[str, Any]]) -> Iterable[tuple[str, str, float]]:
        for row in rows:
            code = row.get("LSOA21CD")
            if not code:
                continue
            code_str = str(code).strip()
            if not code_str.startswith("E"):
                # This technical mirror is explicitly England-only.
                continue
            place_id = f"lsoa21:{code_str}"

            for column, indicator_key in PPFI_COLUMNS.items():
                decile = _coerce_decile(row.get(column))
                if decile is not None:
                    yield (place_id, indicator_key, decile)

    async def _upsert_values(self, values: list[tuple[str, str, float]]) -> tuple[int, int]:
        if not values:
            return (0, 0)

        candidate_ids = {place_id for place_id, _, _ in values}
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text("SELECT id FROM geography.place WHERE id = ANY(:ids)"),
                    {"ids": list(candidate_ids)},
                )
            ).all()
        known = {r.id for r in rows}

        retrieved_at = datetime.now(tz=UTC)
        caveats_json = json.dumps(CAVEATS)
        params = [
            {
                "place_id": place_id,
                "indicator_key": indicator_key,
                "period": PERIOD,
                "value": value,
                "source_id": self.source_id,
                "retrieved_at": retrieved_at,
                "caveats": caveats_json,
            }
            for place_id, indicator_key, value in values
            if place_id in known
        ]
        skipped = len(values) - len(params)
        if not params:
            return (0, skipped)

        upsert_sql = text(
            "INSERT INTO data.indicator_value "
            "(place_id, indicator_key, period, value, source_id, retrieved_at, caveats) "
            "VALUES (:place_id, :indicator_key, :period, :value, :source_id, "
            "        :retrieved_at, CAST(:caveats AS jsonb)) "
            "ON CONFLICT (place_id, indicator_key, period) "
            "DO UPDATE SET value = EXCLUDED.value, "
            "              retrieved_at = EXCLUDED.retrieved_at, "
            "              source_id = EXCLUDED.source_id, "
            "              caveats = EXCLUDED.caveats"
        )
        async with self._engine.begin() as conn:
            for i in range(0, len(params), UPSERT_CHUNK):
                await conn.execute(upsert_sql, params[i : i + UPSERT_CHUNK])
        return (len(params), skipped)


def _coerce_decile(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(value) or value < 1 or value > 10 or not value.is_integer():
        return None
    return value
