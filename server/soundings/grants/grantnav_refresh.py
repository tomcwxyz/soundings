"""Download and refresh Soundings' full GrantNav grant index.

360Giving documents GrantNav whole-dataset downloads as a supported bulk
access route. The export is several hundred MB and growing, so this module
streams it to disk and hands the completed file to the existing bounded-batch
importer rather than buffering it in memory.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Final

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.grants.import_grantnav_csv import import_grantnav_csv

_log = logging.getLogger("soundings.grants.grantnav_refresh")

GRANTNAV_FULL_CSV_URL: Final = "https://grantnav.threesixtygiving.org/search.csv"
# A full GrantNav export is several hundred MB. Weekly keeps Soundings useful
# without paying the bandwidth/DB-write cost of rebuilding ~1.5m grants daily.
# Deployments that need fresher data can opt into a tighter cadence.
DEFAULT_GRANT_INDEX_REFRESH_CRON: Final = "30 5 * * 1"
GRANT_INDEX_REFRESH_CRON: Final = os.getenv(
    "SOUNDINGS_GRANT_INDEX_REFRESH_CRON",
    DEFAULT_GRANT_INDEX_REFRESH_CRON,
)
DOWNLOAD_CHUNK_BYTES: Final = 1024 * 1024
MIN_VALID_EXPORT_BYTES: Final = 256
_REQUIRED_HEADERS: Final = frozenset(
    {
        "Identifier",
        "Amount Awarded",
        "Award Date",
        "Funding Org:Identifier",
        "Recipient Org:Identifier",
    }
)


def _validate_download(path: Path) -> None:
    """Fail closed before a bad response can replace a healthy full index."""
    size = path.stat().st_size
    if size < MIN_VALID_EXPORT_BYTES:
        raise ValueError(f"GrantNav export is unexpectedly small ({size} bytes)")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        first_line = handle.readline()
    headers = {item.strip().strip('"') for item in first_line.split(",")}
    missing = sorted(_REQUIRED_HEADERS - headers)
    if missing:
        raise ValueError("GrantNav export is missing required columns: " + ", ".join(missing))


async def download_grantnav_csv(
    destination: Path,
    *,
    url: str = GRANTNAV_FULL_CSV_URL,
    http_client: httpx.AsyncClient | None = None,
) -> int:
    """Stream GrantNav's CSV export to ``destination`` and return bytes written."""
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(connect=30.0, read=600.0, write=60.0, pool=30.0),
    )
    written = 0
    try:
        async with client.stream(
            "GET",
            url,
            headers={
                "Accept": "text/csv,application/csv;q=0.9,*/*;q=0.1",
                "User-Agent": "Soundings/360Giving-index (+https://github.com/tomcwxyz/soundings)",
            },
        ) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                async for chunk in response.aiter_bytes(DOWNLOAD_CHUNK_BYTES):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    written += len(chunk)
        _validate_download(destination)
        return written
    finally:
        if owns_client:
            await client.aclose()


async def refresh_grant_index(
    engine: AsyncEngine,
    *,
    url: str = GRANTNAV_FULL_CSV_URL,
    http_client: httpx.AsyncClient | None = None,
    temp_dir: Path | None = None,
) -> int:
    """Download a complete GrantNav export and atomically refresh the index.

    The importer only removes stale grants after the new CSV has imported
    successfully. Download/validation/import failures therefore leave the
    previous full index intact.
    """
    fd, raw_path = tempfile.mkstemp(
        prefix="soundings-grantnav-",
        suffix=".csv",
        dir=str(temp_dir) if temp_dir else None,
    )
    os.close(fd)
    path = Path(raw_path)
    try:
        bytes_written = await download_grantnav_csv(
            path,
            url=url,
            http_client=http_client,
        )
        _log.info("GrantNav export downloaded: %s bytes", bytes_written)
        rows = await import_grantnav_csv(engine, path, full_corpus=True)
        _log.info("GrantNav full index refreshed: %s rows", rows)
        return rows
    finally:
        path.unlink(missing_ok=True)
