"""Give Food distribution-location client.

Uses Give Food's documented locations API, with their regularly published
GeoJSON snapshot as a mirror. Both contain individual locations, not just
parent food-bank organisations. Never silently interpret an error as zero.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

LOCATIONS_URL = "https://www.givefood.org.uk/api/2/locations/"
MIRROR_URL = "https://raw.githubusercontent.com/givefood/data/main/locations.geojson"
GIVEFOOD_HEADERS = {
    "User-Agent": "Soundings/1.0 (https://github.com/tomcwxyz/soundings)",
    "Accept": "application/json, application/geo+json",
}
_log = logging.getLogger(__name__)


class GiveFoodUnavailableError(RuntimeError):
    """No usable location-level data was obtained from either source."""


def _coordinates(row: dict[str, Any]) -> tuple[float | None, float | None]:
    location = row.get("lat_lng")
    if isinstance(location, str) and "," in location:
        lat_str, lng_str = location.split(",", 1)
        try:
            lat, lng = float(lat_str), float(lng_str)
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                return lat, lng
        except ValueError:
            pass

    geometry = row.get("geometry")
    if isinstance(geometry, dict) and geometry.get("type") == "Point":
        coords = geometry.get("coordinates")
        if isinstance(coords, list) and len(coords) >= 2:
            try:
                lng, lat = float(coords[0]), float(coords[1])
                if -90 <= lat <= 90 and -180 <= lng <= 180:
                    return lat, lng
            except (TypeError, ValueError):
                pass
    return None, None


def _trim(row: dict[str, Any]) -> dict[str, Any]:
    """Normalise the API list and GeoJSON feature formats to location rows."""
    props = row.get("properties")
    item = props if isinstance(props, dict) else row
    # The GeoJSON feature's geometry sits alongside its properties.
    coordinate_record = dict(item)
    if row.get("type") == "Feature":
        coordinate_record["geometry"] = row.get("geometry")
    lat, lng = _coordinates(coordinate_record)
    parent = item.get("foodbank")
    parent = parent if isinstance(parent, dict) else {}
    organisation = (
        item.get("organisation_name") or parent.get("name") or item.get("organisation") or ""
    )
    name = item.get("location_name") or item.get("name") or organisation or "Food bank"
    lsoa = item.get("lsoa")
    urls = item.get("urls")
    urls = urls if isinstance(urls, dict) else {}
    parent_urls = parent.get("urls")
    parent_urls = parent_urls if isinstance(parent_urls, dict) else {}
    return {
        "lat": lat,
        "lng": lng,
        "postcode": item.get("postcode"),
        "lsoa": lsoa if isinstance(lsoa, str) and lsoa else None,
        "name": str(name).strip() or "Food bank",
        "org": str(organisation).strip() or None,
        "url": urls.get("html") or parent_urls.get("html"),
    }


def _parse_locations(payload: Any) -> list[dict[str, Any]]:
    raw_rows: Any
    if isinstance(payload, list):
        raw_rows = payload
    elif isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
        raw_rows = payload.get("features")
    elif isinstance(payload, dict):
        raw_rows = payload.get("results", payload.get("locations"))
    else:
        raw_rows = None

    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("response has no location list")
    rows = [_trim(row) for row in raw_rows if isinstance(row, dict)]
    if not rows or not any(row["lat"] is not None for row in rows):
        raise ValueError("response has no usable location coordinates")
    return rows


def _error_detail(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.RequestError):
        return f"network {type(exc).__name__}"
    return f"invalid response ({exc})"


class GiveFoodClient:
    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def fetch_foodbanks(self) -> list[dict[str, Any]]:
        """Fetch UK distribution sites; use Give Food's GeoJSON mirror if needed."""
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=60.0)
        failures: list[str] = []
        try:
            for source, url in (("api", LOCATIONS_URL), ("geojson_mirror", MIRROR_URL)):
                try:
                    response = await client.get(
                        url, headers=GIVEFOOD_HEADERS, follow_redirects=True
                    )
                    response.raise_for_status()
                    rows = _parse_locations(response.json())
                except (httpx.HTTPError, ValueError, TypeError) as exc:
                    detail = _error_detail(exc)
                    failures.append(f"{source}: {detail}")
                    _log.warning(
                        "givefood location fetch failed source=%s detail=%s", source, detail
                    )
                    continue
                if source != "api":
                    _log.warning("givefood using mirror source=%s records=%d", source, len(rows))
                else:
                    _log.info("givefood API locations fetched records=%d", len(rows))
                return rows
        finally:
            if owns_client:
                await client.aclose()

        detail = "; ".join(failures)
        _log.error("givefood no usable location data: %s", detail)
        raise GiveFoodUnavailableError(f"Give Food location sources failed ({detail})")
