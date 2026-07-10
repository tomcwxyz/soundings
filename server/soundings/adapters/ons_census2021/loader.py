"""ons.census2021 loader.

Same shape as the MYE loader: walks the Census-sourced indicator mappings,
calls Nomis per (level, place), upserts into `data.indicator_value`. Only
covers England + Wales — Scottish geographies silently skipped (NRS 2022
is a separate exercise; tracked as a coverage gap caveat in the catalogue).
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.base import LoaderAdapter, LoaderResult
from soundings.adapters.nomis.client import NomisClient
from soundings.adapters.nomis.mapping import NomisMapping, load_nomis_mapping
from soundings.db.models.data import IndicatorValue

SOURCE_ID = "ons.census2021"
DEFAULT_MAPPING_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent.parent / "catalogue" / "nomis-mapping.yaml"
)


class OnsCensus2021Loader(LoaderAdapter):
    source_id = SOURCE_ID

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        nomis_client: NomisClient | None = None,
        indicator_keys: list[str] | None = None,
        mapping_path: Path | None = None,
        place_filter: list[str] | None = None,
    ) -> None:
        super().__init__(engine)
        self._nomis = nomis_client or NomisClient()
        self._indicator_keys = indicator_keys
        self._mapping_path = mapping_path or DEFAULT_MAPPING_PATH
        self._place_filter = place_filter

    async def load(self, run_id: str | None = None) -> LoaderResult:
        mappings = [
            m for m in load_nomis_mapping(self._mapping_path) if m.source_id == self.source_id
        ]
        if self._indicator_keys is not None:
            keys = set(self._indicator_keys)
            mappings = [m for m in mappings if m.indicator_key in keys]

        rows_written = 0
        for mapping in mappings:
            rows_written += await self._load_one(mapping)
        return LoaderResult(rows_written=rows_written)

    async def _load_one(self, mapping: NomisMapping) -> int:
        rows_written = 0
        for place_type in mapping.geography_type_codes:
            place_codes = await self._place_codes_for_type(place_type)
            for place_code in place_codes:
                if not place_code.startswith(("E", "W")):
                    # Census 2021 covers England + Wales only.
                    continue
                obs = await self._fetch_observations(mapping, place_code)
                rows_written += await self._upsert_obs(mapping, place_type, obs)
        return rows_written

    async def _place_codes_for_type(self, place_type: str) -> list[str]:
        async with self._engine.connect() as conn:
            params: dict[str, Any] = {"t": place_type}
            sql = "SELECT code FROM geography.place WHERE type = :t"
            if self._place_filter:
                sql += " AND id = ANY(:filter)"
                params["filter"] = self._place_filter
            rows = (await conn.execute(text(sql), params)).all()
        return [r.code for r in rows]

    async def _fetch_observations(
        self, mapping: NomisMapping, place_code: str
    ) -> list[dict[str, Any]]:
        payload = await self._nomis.get_observations(
            dataset_id=mapping.dataset_id,
            geography=place_code,
            measures=mapping.measures,
            time=mapping.period or "2021",
            **mapping.extra_params,
        )
        obs: list[dict[str, Any]] = payload.get("obs", [])
        return obs

    async def _upsert_obs(
        self,
        mapping: NomisMapping,
        place_type: str,
        obs: list[dict[str, Any]],
    ) -> int:
        if not obs:
            return 0

        # --- Computed indicators -------------------------------------------
        # Some Census indicators derive a single value from one or more Nomis
        # cells rather than reading a single filtered observation. Handle these
        # before the standard single-cell path.
        if mapping.computation == "complement":
            return await self._upsert_computed(
                mapping,
                place_type,
                obs,
                fn=lambda v: 100.0 - v,
            )
        if mapping.computation == "sum_codes":
            codes = set(mapping.sum_codes or [])
            return await self._upsert_sum_codes(mapping, place_type, obs, codes)

        # --- Standard single-cell path -------------------------------------
        rows = []
        retrieved = datetime.now(tz=UTC)
        for o in obs:
            geo_code = o.get("geography", {}).get("geogcode")
            if not geo_code:
                continue
            value = o.get("obs_value", {}).get("value")
            # Nomis returns small percentages as strings (e.g. "0.1") and large
            # values as floats — coerce defensively before any arithmetic.
            if value is not None:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
                if mapping.value_scale is not None:
                    value = value * mapping.value_scale
            period = o.get("time", {}).get("description") or mapping.period or "2021"
            rows.append(
                {
                    "place_id": f"{place_type}:{geo_code}",
                    "indicator_key": mapping.indicator_key,
                    "period": str(period),
                    "value": value,
                    "source_id": self.source_id,
                    "retrieved_at": retrieved,
                    "loader_run_id": None,
                    "caveats": ["Census 2021 covers England and Wales only."],
                }
            )
        if not rows:
            return 0
        # Some Census mappings (e.g. ethnic group, household composition) don't
        # filter to a single cell, so Nomis returns one row per dimension value
        # all colliding on the same (place_id, indicator_key, period). That
        # signals an unverified mapping per HANDOFF — skip the whole batch
        # rather than write a misleading value.
        keys = [(r["place_id"], r["indicator_key"], r["period"]) for r in rows]
        if len(set(keys)) < len(rows):
            return 0
        async with self._engine.begin() as conn:
            stmt = insert(IndicatorValue).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    IndicatorValue.place_id,
                    IndicatorValue.indicator_key,
                    IndicatorValue.period,
                ],
                set_={
                    "value": stmt.excluded.value,
                    "retrieved_at": stmt.excluded.retrieved_at,
                    "source_id": stmt.excluded.source_id,
                },
            )
            await conn.execute(stmt)
        return len(rows)

    async def _upsert_computed(
        self,
        mapping: NomisMapping,
        place_type: str,
        obs: list[dict[str, Any]],
        *,
        fn: Any,
    ) -> int:
        """Apply a scalar transform (e.g. 100 - v) to each observation and
        upsert. Used for complement indicators like non-white-British share."""
        rows = []
        retrieved = datetime.now(tz=UTC)
        for o in obs:
            geo_code = o.get("geography", {}).get("geogcode")
            if not geo_code:
                continue
            raw = o.get("obs_value", {}).get("value")
            if raw is None:
                continue
            try:
                raw = float(raw)
            except (TypeError, ValueError):
                continue
            value = fn(raw)
            if mapping.value_scale is not None:
                value = value * mapping.value_scale
            period = o.get("time", {}).get("description") or mapping.period or "2021"
            rows.append(
                {
                    "place_id": f"{place_type}:{geo_code}",
                    "indicator_key": mapping.indicator_key,
                    "period": str(period),
                    "value": value,
                    "source_id": self.source_id,
                    "retrieved_at": retrieved,
                    "loader_run_id": None,
                    "caveats": ["Census 2021 covers England and Wales only."],
                }
            )
        if not rows:
            return 0
        async with self._engine.begin() as conn:
            stmt = insert(IndicatorValue).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    IndicatorValue.place_id,
                    IndicatorValue.indicator_key,
                    IndicatorValue.period,
                ],
                set_={
                    "value": stmt.excluded.value,
                    "retrieved_at": stmt.excluded.retrieved_at,
                    "source_id": stmt.excluded.source_id,
                },
            )
            await conn.execute(stmt)
        return len(rows)

    async def _upsert_sum_codes(
        self,
        mapping: NomisMapping,
        place_type: str,
        obs: list[dict[str, Any]],
        codes: set[str],
    ) -> int:
        """Sum obs_values for the specified dimension codes, grouped by place.
        Used for multi-cell indicators like overcrowding (rating -1 + -2 or less)."""
        # Group by geo_code, summing values for matching codes
        place_sums: dict[str, float] = {}
        periods: dict[str, str] = {}
        for o in obs:
            geo_code = o.get("geography", {}).get("geogcode")
            if not geo_code:
                continue
            # Find the dimension value code — it's the field that isn't
            # one of the standard metadata fields.
            known = {
                "dataset",
                "measures",
                "obs_value",
                "geography",
                "time",
                "freq",
                "time_format",
                "unit",
                "obs_status",
                "obs_conf",
                "urn",
            }
            dim_code = None
            for key in o:
                if key not in known:
                    val = o[key]
                    if isinstance(val, dict):
                        dim_code = str(val.get("value", ""))
                    else:
                        dim_code = str(val)
                    break
            if dim_code is None or dim_code not in codes:
                continue
            raw = o.get("obs_value", {}).get("value")
            if raw is None:
                continue
            try:
                raw = float(raw)
            except (TypeError, ValueError):
                continue
            place_sums[geo_code] = place_sums.get(geo_code, 0.0) + raw
            period = o.get("time", {}).get("description") or mapping.period or "2021"
            periods[geo_code] = str(period)

        if not place_sums:
            return 0
        rows = []
        retrieved = datetime.now(tz=UTC)
        for geo_code, total in place_sums.items():
            value = total
            if mapping.value_scale is not None:
                value = value * mapping.value_scale
            rows.append(
                {
                    "place_id": f"{place_type}:{geo_code}",
                    "indicator_key": mapping.indicator_key,
                    "period": periods[geo_code],
                    "value": value,
                    "source_id": self.source_id,
                    "retrieved_at": retrieved,
                    "loader_run_id": None,
                    "caveats": ["Census 2021 covers England and Wales only."],
                }
            )
        async with self._engine.begin() as conn:
            stmt = insert(IndicatorValue).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    IndicatorValue.place_id,
                    IndicatorValue.indicator_key,
                    IndicatorValue.period,
                ],
                set_={
                    "value": stmt.excluded.value,
                    "retrieved_at": stmt.excluded.retrieved_at,
                    "source_id": stmt.excluded.source_id,
                },
            )
            await conn.execute(stmt)
        return len(rows)
