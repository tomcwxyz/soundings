"""Loads ONS Code History Database area-changes into geography.code_change.

CHD ships as a periodic bulk download. The relevant Geography History CSV
contains rows mapping old codes to new codes with a change type and an
effective date. Field names and date formats vary slightly between editions;
we accept the common variants conservatively and fail if a CHD archive cannot
produce any history rows rather than silently retaining stale lineage.
"""

import csv
import io
from datetime import date, datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.base import LoaderAdapter, LoaderResult

CHD_URL = (
    "https://www.ons.gov.uk/file"
    "?uri=/methodology/geography/geographicalproducts/"
    "namescodesandlookups/codehistorydatabasechd/"
    "codehistorydatabasechd.zip"
)


# Field synonyms — we read whichever name is present.
OLD_CODE_FIELDS = ("GEOGCD_O", "GEOGCDO", "OLD_CODE")
NEW_CODE_FIELDS = ("GEOGCD_N", "GEOGCDN", "NEW_CODE")
TYPE_FIELDS = ("GEOGCHGTYPE", "CHGTYPE", "CHANGE_TYPE")
DATE_FIELDS = ("EFFECTIVE_DATE", "EFFDATE", "OPER_DATE")
NOTES_FIELDS = ("NOTES", "NOTE")
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%Y%m%d")


def _pick(row: dict[str, str], candidates: tuple[str, ...]) -> str | None:
    for c in candidates:
        if row.get(c):
            return row[c]
    return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


class OnsGeographyCodeChangeLoader(LoaderAdapter):
    source_id = "ons.geography"

    def __init__(
        self,
        engine: AsyncEngine,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._engine = engine
        self._client = http_client

    async def load(self, run_id: str | None = None) -> LoaderResult:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=120.0, follow_redirects=True)
        try:
            response = await client.get(CHD_URL)
            response.raise_for_status()
            return await self.load_from_zip_bytes(response.content)
        finally:
            if owns_client:
                await client.aclose()

    async def load_from_zip_bytes(self, blob: bytes) -> LoaderResult:
        import zipfile

        rows: list[dict[str, Any]] = []
        candidate_files: list[str] = []
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            for name in zf.namelist():
                lowered = name.lower()
                if not lowered.endswith(".csv"):
                    continue
                if "change" not in lowered and "history" not in lowered:
                    continue
                candidate_files.append(name)
                rows.extend(self._parse_csv(zf.read(name)))

        if not rows:
            names = ", ".join(candidate_files) if candidate_files else "none"
            raise ValueError(
                "CHD archive contained no parseable Geography History rows; "
                f"candidate CSV files: {names}"
            )
        return await self._upsert(rows)

    async def load_from_bytes(self, blob: bytes) -> LoaderResult:
        rows = self._parse_csv(blob)
        return await self._upsert(rows)

    @staticmethod
    def _parse_csv(blob: bytes) -> list[dict[str, Any]]:
        text_stream = io.StringIO(blob.decode("utf-8-sig"))
        reader = csv.DictReader(text_stream)
        out: list[dict[str, Any]] = []
        for row in reader:
            old = _pick(row, OLD_CODE_FIELDS)
            new = _pick(row, NEW_CODE_FIELDS)
            ctype = _pick(row, TYPE_FIELDS)
            eff = _parse_date(_pick(row, DATE_FIELDS))
            if not (old and new and ctype and eff):
                continue
            out.append(
                {
                    "old_code": old,
                    "new_code": new,
                    "change_type": ctype,
                    "effective_date": eff,
                    "notes": _pick(row, NOTES_FIELDS),
                }
            )
        return out

    async def _upsert(self, rows: list[dict[str, Any]]) -> LoaderResult:
        if not rows:
            return LoaderResult(rows_written=0)
        async with self._engine.begin() as conn:
            # Idempotent: replace the table with the latest CHD edition.
            await conn.execute(text("TRUNCATE TABLE geography.code_change"))
            await conn.execute(
                text(
                    "INSERT INTO geography.code_change "
                    "(old_code, new_code, change_type, effective_date, notes) "
                    "VALUES (:old_code, :new_code, :change_type, :effective_date, :notes)"
                ),
                rows,
            )
        return LoaderResult(rows_written=len(rows))
