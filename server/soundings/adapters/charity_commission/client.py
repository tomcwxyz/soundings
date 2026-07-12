"""Charity Commission for England and Wales — bulk register client.

Downloads the public monthly `publicextract.charity.zip` and yields
one dict per active main-entry charity. Anonymous — no API key. CC
publishes the per-table ZIPs on an Azure Blob Storage endpoint linked
from the register-download landing page.

The API alternative is detail-lookup-only (no search-by-area
endpoint), so for Phase 4 the bulk download is the documented
carve-out from the project's API-first principle. See
`docs/plans/2026-05-12-soundings-v1-phase-4-plan.md` for the rationale.

Archive structure: one ZIP per CC table; we use just
`publicextract.charity.zip` (~43MB compressed, ~160MB extracted,
~220k rows). The ZIP contains a single tab-delimited file
`publicextract.charity.txt` with columns including
`registered_charity_number`, `linked_charity_number`, `charity_name`,
`charity_registration_status`, `charity_contact_postcode`,
`charity_activities`.

Filtering at the source: we yield only main-entry rows
(`linked_charity_number = '0'`) with status `'Registered'`. Linked
subsidiaries inherit the same registered number with a non-zero
suffix; we don't want them in the v1 active-count aggregate.

Streaming notes: ~43MB compressed lives in memory because ZIP's
central-directory record is at the end of the archive. The extracted
TXT is iterated line-by-line, not slurped into memory all at once.
"""

import csv
import io
import math
import zipfile
from collections.abc import AsyncIterator
from typing import Any

import httpx

# Some CC fields (notably `charity_activities`, the free-text
# description) exceed Python's default 128KB csv.field_size_limit.
# 16MB is comfortably above any plausible single-field size.
csv.field_size_limit(16 * 1024 * 1024)

# Azure-blob-hosted bulk extract. Each CC table is a separate ZIP at
# /data/txt/publicextract.{table}.zip; Phase 4 only needs the main
# charity table.
CC_CHARITY_BULK_URL = (
    "https://ccewuksprdoneregsadata1.blob.core.windows.net/data/txt/publicextract.charity.zip"
)
CHARITY_TXT = "publicextract.charity.txt"

# Area-of-operation bulk extract — maps each charity to the geographic
# areas it operates in (Local Authority, Region, Country).  We use the
# "Local Authority" rows to populate data.organisation_operates_in with
# a richer mapping than registered-address-only.
CC_AREA_OF_OPERATION_URL = (
    "https://ccewuksprdoneregsadata1.blob.core.windows.net/data/txt"
    "/publicextract.charity_area_of_operation.zip"
)
AREA_OF_OPERATION_TXT = "publicextract.charity_area_of_operation.txt"

# Classification bulk extract — structured cause codes (What/Who/How)
# per charity.  1.7M rows, ~7MB compressed.
CC_CLASSIFICATION_URL = (
    "https://ccewuksprdoneregsadata1.blob.core.windows.net/data/txt"
    "/publicextract.charity_classification.zip"
)
CLASSIFICATION_TXT = "publicextract.charity_classification.txt"


class CharityCommissionBulkClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        url: str = CC_CHARITY_BULK_URL,
    ) -> None:
        self._client = http_client
        self._owns_client = http_client is None
        self._url = url

    async def iter_active_charities(self) -> AsyncIterator[dict[str, Any]]:
        """Yield one dict per active main-entry charity.

        Each yielded dict has stable keys: `registration_number`,
        `name`, `postcode`, `status`, `classification` (list[str]).
        """
        client = self._client or httpx.AsyncClient(timeout=120.0)
        try:
            response = await client.get(self._url, follow_redirects=True)
            response.raise_for_status()
            archive = zipfile.ZipFile(io.BytesIO(response.content))

            with archive.open(CHARITY_TXT) as fh:
                text = io.TextIOWrapper(fh, encoding="utf-8", newline="")
                reader = csv.DictReader(text, delimiter="\t")
                for row in reader:
                    if row.get("linked_charity_number", "").strip() != "0":
                        continue  # subsidiary entry
                    if row.get("charity_registration_status", "").strip() != "Registered":
                        continue
                    reg = row.get("registered_charity_number", "").strip()
                    if not reg:
                        continue
                    yield {
                        "registration_number": reg,
                        "name": row.get("charity_name", "").strip(),
                        "postcode": row.get("charity_contact_postcode", "").strip(),
                        "status": "Registered",
                        "classification": _activities_to_classification(
                            row.get("charity_activities", "")
                        ),
                        "latest_income": _coerce_float(row.get("latest_income")),
                        "date_of_registration": _blank_to_none(row.get("date_of_registration")),
                        "date_of_removal": _blank_to_none(row.get("date_of_removal")),
                    }
        finally:
            if self._owns_client:
                await client.aclose()

    async def iter_area_of_operation(self) -> AsyncIterator[dict[str, Any]]:
        """Yield one dict per Local-Authority area-of-operation row.

        Each yielded dict has: ``registration_number`` (str), ``area_description``
        (str — the CC LA name, e.g. ``"Durham"``, ``"Birmingham City"``).

        Only ``geographic_area_type == "Local Authority"`` rows are yielded.
        Region-level rows (``"Throughout England And Wales"`` etc.) and
        international Country rows are skipped — they don't map to a single
        LTLA.
        """
        client = self._client or httpx.AsyncClient(timeout=120.0)
        try:
            response = await client.get(CC_AREA_OF_OPERATION_URL, follow_redirects=True)
            response.raise_for_status()
            archive = zipfile.ZipFile(io.BytesIO(response.content))
            with archive.open(AREA_OF_OPERATION_TXT) as fh:
                text = io.TextIOWrapper(fh, encoding="utf-8", newline="")
                reader = csv.DictReader(text, delimiter="\t")
                for row in reader:
                    if row.get("geographic_area_type", "").strip() != "Local Authority":
                        continue
                    reg = row.get("registered_charity_number", "").strip()
                    if not reg:
                        continue
                    yield {
                        "registration_number": reg,
                        "area_description": row.get("geographic_area_description", "").strip(),
                    }
        finally:
            if self._owns_client:
                await client.aclose()

    async def iter_classifications(self) -> AsyncIterator[dict[str, str]]:
        """Yield one dict per classification row (main entries only).

        Each dict has: ``registration_number``, ``classification_type``
        ("What" / "Who" / "How"), ``classification_code``, and
        ``classification_label``.

        Only main-entry rows (``linked_charity_number == '0'``) are
        yielded — linked subsidiaries are filtered out to match the
        ``data.organisation`` table which only stores main entries.
        """
        client = self._client or httpx.AsyncClient(timeout=120.0)
        try:
            response = await client.get(CC_CLASSIFICATION_URL, follow_redirects=True)
            response.raise_for_status()
            archive = zipfile.ZipFile(io.BytesIO(response.content))
            with archive.open(CLASSIFICATION_TXT) as fh:
                text = io.TextIOWrapper(fh, encoding="utf-8", newline="")
                reader = csv.DictReader(text, delimiter="\t")
                for row in reader:
                    if row.get("linked_charity_number", "").strip() != "0":
                        continue
                    reg = row.get("registered_charity_number", "").strip()
                    if not reg:
                        continue
                    yield {
                        "registration_number": reg,
                        "classification_type": row.get("classification_type", "").strip(),
                        "classification_code": row.get("classification_code", "").strip(),
                        "classification_label": row.get("classification_description", "").strip(),
                    }
        finally:
            if self._owns_client:
                await client.aclose()


def _activities_to_classification(raw: str) -> list[str]:
    """The bulk doesn't ship classification codes directly — those live
    in a separate `publicextract.charity_classification.zip` table.
    For v1, use the free-text `charity_activities` field as a single
    classification entry. Phase 5+ enrichment can join the codes table
    for structured classification."""
    cleaned = raw.strip()
    if not cleaned:
        return []
    return [cleaned]


def _coerce_float(raw: str | None) -> float | None:
    """CC bulk leaves `latest_income` blank for charities that haven't
    filed an annual return. Treat blank + non-numeric as None rather
    than 0.0, so downstream aggregates can exclude them cleanly. NaN
    and Inf strings parse as floats but break SQL aggregates, so
    coerce them to None too. Negative values are CC data anomalies
    (gross income should never be negative) — also coerce to None
    so aggregates aren't biased."""
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    if value < 0:
        return None
    return value


def _blank_to_none(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = raw.strip()
    return cleaned or None
