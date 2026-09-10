"""Load terminated ONS/GSS geography codes as dated historical places."""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.base import LoaderAdapter, LoaderResult
from soundings.adapters.ons_geography.chd_archive import (
    inspect_chd_archive,
    normalise_chd_header,
)
from soundings.adapters.ons_geography.chd_source import (
    CHD_CURRENT_ARCGIS_URL,
    fetch_chd_archive,
    parse_chd_date,
)
from soundings.db.models.geography import Place


def historical_place_type(entity_code: str) -> str:
    """Return a source-faithful Soundings type for one GSS entity code."""
    normalised = entity_code.strip().lower()
    if not normalised or not normalised.isalnum():
        raise ValueError("invalid GSS entity code")
    return f"gss_{normalised}"


def historical_place_id(entity_code: str, code: str) -> str:
    """Return the stable ID used for a terminated CHD place."""
    place_type = historical_place_type(entity_code)
    clean_code = code.strip()
    if not clean_code:
        raise ValueError("historical place code is required")
    return f"{place_type}:{clean_code}"


@dataclass
class _PlaceAggregate:
    code: str
    entity_code: str
    name: str
    name_date: date
    valid_from: date
    valid_to: date | None


def _normalise_row(row: dict[str, str]) -> dict[str, str]:
    return {
        normalise_chd_header(str(key)): "" if value is None else str(value).strip()
        for key, value in row.items()
        if key is not None
    }


def _place_row(
    row: dict[str, str],
) -> tuple[str, str, str, date, date | None] | None:
    normalised = _normalise_row(row)
    code = normalised.get("GEOGCD", "")
    name = normalised.get("GEOGNM", "")
    entity_code = normalised.get("ENTITYCD", "")
    operative = parse_chd_date(normalised.get("OPER_DATE"))
    term_value = normalised.get("TERM_DATE", "")
    term_date = parse_chd_date(term_value)

    if not code or not name or not entity_code or operative is None:
        return None
    if not entity_code.isalnum():
        return None
    if term_value and term_date is None:
        return None

    valid_to = term_date + timedelta(days=1) if term_date is not None else None
    return code, name, entity_code, operative, valid_to


class OnsGeographyHistoricalPlacesLoader(LoaderAdapter):
    """Create dated GSS identities for terminated codes absent from the current spine."""

    source_id = "ons.geography"

    def __init__(
        self,
        engine: AsyncEngine,
        http_client: httpx.AsyncClient | None = None,
        url: str = CHD_CURRENT_ARCGIS_URL,
    ) -> None:
        self._engine = engine
        self._client = http_client
        self._url = url

    async def load(self, run_id: str | None = None) -> LoaderResult:
        blob = await fetch_chd_archive(self._url, http_client=self._client)
        return await self.load_from_zip_bytes(blob)

    async def load_from_zip_bytes(self, blob: bytes) -> LoaderResult:
        inventory = inspect_chd_archive(blob, sample_size=0)
        if not inventory.change_history_tables:
            names = ", ".join(table.name for table in inventory.tables) or "none"
            raise ValueError(
                f"CHD archive contained no recognised ChangeHistory table; CSV files: {names}"
            )

        aggregates: dict[str, _PlaceAggregate] = {}
        source_rows = 0
        invalid_rows = 0

        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            for table in inventory.change_history_tables:
                with zf.open(table.name) as member:
                    with io.TextIOWrapper(
                        member,
                        encoding="utf-8-sig",
                        errors="replace",
                        newline="",
                    ) as stream:
                        for row in csv.DictReader(stream):
                            source_rows += 1
                            parsed = _place_row(row)
                            if parsed is None:
                                invalid_rows += 1
                                continue
                            code, name, entity_code, operative, valid_to = parsed
                            existing = aggregates.get(code)
                            if existing is None:
                                aggregates[code] = _PlaceAggregate(
                                    code=code,
                                    entity_code=entity_code,
                                    name=name,
                                    name_date=operative,
                                    valid_from=operative,
                                    valid_to=valid_to,
                                )
                                continue
                            if existing.entity_code != entity_code:
                                invalid_rows += 1
                                continue
                            existing.valid_from = min(existing.valid_from, operative)
                            if operative >= existing.name_date:
                                existing.name = name
                                existing.name_date = operative
                            if existing.valid_to is None or valid_to is None:
                                existing.valid_to = None
                            else:
                                existing.valid_to = max(existing.valid_to, valid_to)

        place_ids_by_code = await self._place_ids_by_code()
        rows: list[dict[str, Any]] = []
        live_codes = 0
        existing_codes = 0
        for aggregate in aggregates.values():
            target_id = historical_place_id(aggregate.entity_code, aggregate.code)
            existing_ids = place_ids_by_code.get(aggregate.code, ())
            if any(place_id != target_id for place_id in existing_ids):
                existing_codes += 1
                continue
            if aggregate.valid_to is None:
                live_codes += 1
                continue

            rows.append(
                {
                    "id": target_id,
                    "type": historical_place_type(aggregate.entity_code),
                    "code": aggregate.code,
                    "name": aggregate.name,
                    "valid_from": aggregate.valid_from,
                    "valid_to": aggregate.valid_to,
                }
            )

        await self._upsert_rows(rows)
        notes = (
            f"source_rows={source_rows}; rows_written={len(rows)}; "
            f"existing_codes={existing_codes}; live_codes={live_codes}; "
            f"invalid_rows={invalid_rows}"
        )
        return LoaderResult(rows_written=len(rows), notes=notes)

    async def _place_ids_by_code(self) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        async with self._engine.connect() as conn:
            rows = (await conn.execute(select(Place.code, Place.id))).all()
        for row in rows:
            grouped.setdefault(str(row.code), []).append(str(row.id))
        return {code: tuple(ids) for code, ids in grouped.items()}

    async def _upsert_rows(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        async with self._engine.begin() as conn:
            stmt = insert(Place).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Place.id],
                set_={
                    "name": stmt.excluded.name,
                    "valid_from": stmt.excluded.valid_from,
                    "valid_to": stmt.excluded.valid_to,
                },
            )
            await conn.execute(stmt)
