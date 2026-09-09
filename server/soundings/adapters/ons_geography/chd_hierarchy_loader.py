"""Load dated direct place containment from the ONS Code History Database.

The live CHD `ChangeHistory.csv` records each geography code with its immediate
`PARENTCD`, operative date and optional termination date. Soundings stores those
source-faithful direct edges and lets historical queries traverse them
recursively, rather than manufacturing transitive rows during ingestion.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timedelta
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
from soundings.db.models.geography import Place, PlaceHierarchy

CHD_CURRENT_ARCGIS_URL = (
    "https://www.arcgis.com/sharing/rest/content/items/"
    "e0bc41722b1a4b76a6ecfff14f91cbb4/data"
)

_DATE_FORMATS = (
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%Y%m%d",
)
_BATCH_SIZE = 5_000


def parse_chd_date(value: str | None) -> date | None:
    """Parse date/timestamp shapes observed across CHD editions."""
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def relationship_from_chd_row(row: dict[str, str]) -> tuple[str, str, date, date | None] | None:
    """Return source codes plus a half-open validity interval for one CHD row."""
    normalised = {
        normalise_chd_header(str(key)): "" if value is None else str(value).strip()
        for key, value in row.items()
        if key is not None
    }
    child_code = normalised.get("GEOGCD", "")
    parent_code = normalised.get("PARENTCD", "")
    valid_from = parse_chd_date(normalised.get("OPER_DATE"))
    term_date = parse_chd_date(normalised.get("TERM_DATE"))
    if not child_code or not parent_code or valid_from is None:
        return None

    # CHD termination dates are the final valid day. Soundings uses half-open
    # intervals, so the exclusive end is the following day.
    valid_to = term_date + timedelta(days=1) if term_date is not None else None
    return child_code, parent_code, valid_from, valid_to


class OnsGeographyHistoricalHierarchyLoader(LoaderAdapter):
    """Ingest dated CHD edges for geography codes Soundings already knows."""

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
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=180.0, follow_redirects=True)
        try:
            response = await client.get(self._url)
            response.raise_for_status()
            return await self.load_from_zip_bytes(response.content)
        finally:
            if owns_client:
                await client.aclose()

    async def load_from_zip_bytes(self, blob: bytes) -> LoaderResult:
        inventory = inspect_chd_archive(blob, sample_size=0)
        if not inventory.change_history_tables:
            names = ", ".join(table.name for table in inventory.tables) or "none"
            raise ValueError(f"CHD archive contained no recognised ChangeHistory table; CSV files: {names}")

        place_ids_by_code = await self._place_ids_by_code()
        source_rows = 0
        rows_without_parent_or_date = 0
        unresolved_rows = 0
        canonical_edges = 0
        buffer: dict[tuple[str, str, date], dict[str, Any]] = {}

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
                            relationship = relationship_from_chd_row(row)
                            if relationship is None:
                                rows_without_parent_or_date += 1
                                continue
                            child_code, parent_code, valid_from, valid_to = relationship
                            child_ids = place_ids_by_code.get(child_code, ())
                            parent_ids = place_ids_by_code.get(parent_code, ())
                            if not child_ids or not parent_ids:
                                unresolved_rows += 1
                                continue

                            for child_id in child_ids:
                                for parent_id in parent_ids:
                                    if child_id == parent_id:
                                        continue
                                    key = (child_id, parent_id, valid_from)
                                    buffer[key] = {
                                        "child_id": child_id,
                                        "parent_id": parent_id,
                                        "valid_from": valid_from,
                                        "valid_to": valid_to,
                                    }
                            if len(buffer) >= _BATCH_SIZE:
                                canonical_edges += await self._upsert_rows(buffer.values())
                                buffer.clear()

        if buffer:
            canonical_edges += await self._upsert_rows(buffer.values())

        notes = (
            f"source_rows={source_rows}; canonical_edges={canonical_edges}; "
            f"unresolved_rows={unresolved_rows}; "
            f"rows_without_parent_or_date={rows_without_parent_or_date}"
        )
        return LoaderResult(rows_written=canonical_edges, notes=notes)

    async def _place_ids_by_code(self) -> dict[str, tuple[str, ...]]:
        grouped: defaultdict[str, list[str]] = defaultdict(list)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(select(Place.code, Place.id))).all()
        for row in rows:
            grouped[str(row.code)].append(str(row.id))
        return {code: tuple(ids) for code, ids in grouped.items()}

    async def _upsert_rows(self, rows: Iterable[dict[str, Any]]) -> int:
        values = list(rows)
        if not values:
            return 0
        async with self._engine.begin() as conn:
            stmt = insert(PlaceHierarchy).values(values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    PlaceHierarchy.child_id,
                    PlaceHierarchy.parent_id,
                    PlaceHierarchy.valid_from,
                ],
                index_where=PlaceHierarchy.valid_from.is_not(None),
                set_={"valid_to": stmt.excluded.valid_to},
            )
            await conn.execute(stmt)
        return len(values)
