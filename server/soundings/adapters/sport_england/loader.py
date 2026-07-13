"""SportEnglandActiveLivesLoader — writes sport/physical-activity
indicators from the Active Lives Adult Survey into
`data.indicator_value`.

Downloads the "Tables 1-5 Levels of Activity" Excel workbook,
parses Table 3 (LA-level activity rates), and upserts three
indicators per LA for the Nov 2024-25 period.

FK-tolerant: GSS codes that don't match an existing `geography.place`
row are skipped, mirroring the FoE and IMD loader pattern.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.base import LoaderAdapter, LoaderResult
from soundings.adapters.sport_england.client import (
    SportEnglandActiveLivesClient,
)

SOURCE_ID = "sport_england"
PERIOD = "2024-2025"  # Active Lives Survey Nov 2024-25
UPSERT_CHUNK = 500

# Indicator keys mapped to client dict fields.
# Rates from Active Lives are stored as 0-1 proportions (e.g. 0.5829 = 58.29%).
_INDICATOR_MAP: dict[str, str] = {
    "sport.active_share": "active_rate",
    "sport.fairly_active_share": "fairly_active_rate",
    "sport.inactive_share": "inactive_rate",
}

# All sport indicators are stored as 0-1 proportions (source data is already 0-1).
# No conversion needed — values are proportions straight from the Excel.


class SportEnglandActiveLivesLoader(LoaderAdapter):
    source_id = SOURCE_ID

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        client: SportEnglandActiveLivesClient | None = None,
    ) -> None:
        super().__init__(engine)
        self._client = client or SportEnglandActiveLivesClient()

    async def load(self, run_id: str | None = None) -> LoaderResult:
        content = await self._client.fetch_workbook()
        rows = list(self._client.read_la_sheet(content))
        values = list(self._extract(rows))
        written, skipped = await self._upsert_values(values)
        notes = f"{skipped} values skipped (place not in spine)" if skipped else None
        return LoaderResult(rows_written=written, notes=notes)

    @staticmethod
    def _extract(
        rows: Iterable[dict[str, Any]],
    ) -> Iterable[tuple[str, str, float]]:
        """Yield (place_id, indicator_key, value) for each populated metric.

        All sport indicators are stored as 0-1 proportions, matching the
        source data format (e.g. 0.5829 = 58.29% active).
        """
        for row in rows:
            gss_code = row.get("gss_code")
            if not gss_code:
                continue
            place_id = f"ltla24:{gss_code}"
            for indicator_key, field in _INDICATOR_MAP.items():
                value = row.get(field)
                if value is None:
                    continue
                yield (place_id, indicator_key, value)

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
        params = [
            {
                "place_id": place_id,
                "indicator_key": indicator_key,
                "period": PERIOD,
                "value": value,
                "source_id": self.source_id,
                "retrieved_at": retrieved_at,
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
            "        :retrieved_at, '[]'::jsonb) "
            "ON CONFLICT (place_id, indicator_key, period) "
            "DO UPDATE SET value = EXCLUDED.value, "
            "              retrieved_at = EXCLUDED.retrieved_at, "
            "              source_id = EXCLUDED.source_id"
        )
        async with self._engine.begin() as conn:
            for i in range(0, len(params), UPSERT_CHUNK):
                await conn.execute(upsert_sql, params[i : i + UPSERT_CHUNK])
        return (len(params), skipped)
