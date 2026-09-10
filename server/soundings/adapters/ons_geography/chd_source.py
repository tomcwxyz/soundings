"""Shared source helpers for the ONS Code History Database CSV collection."""

from __future__ import annotations

from datetime import date, datetime

import httpx

CHD_CURRENT_ARCGIS_URL = (
    "https://www.arcgis.com/sharing/rest/content/items/e0bc41722b1a4b76a6ecfff14f91cbb4/data"
)

_DATE_FORMATS = (
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%Y%m%d",
)


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


async def fetch_chd_archive(
    url: str = CHD_CURRENT_ARCGIS_URL,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> bytes:
    """Download the current CHD archive, reusing a caller-owned client when supplied."""
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=180.0, follow_redirects=True)
    try:
        response = await client.get(url)
        response.raise_for_status()
        return response.content
    finally:
        if owns_client:
            await client.aclose()
