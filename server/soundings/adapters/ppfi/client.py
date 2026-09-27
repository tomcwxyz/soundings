"""Client for the HASP Priority Places for Food Index (PPFI) v2.1.

The public HASP catalogue currently routes Tier-0 downloads through the
MetadataWorks browser/login flow rather than exposing a stable unauthenticated
asset URL. For this source spike we therefore read the England LSOA slice from
HASP's own PPFI-IMD Explorer repository, pinned to a known commit.

That GeoJSON contains the published PPFI v2.1 combined and seven domain
deciles. Geometry is ignored by Soundings: the LSOA code joins directly to the
existing geography spine.

Primary dataset/citation:
  https://data.hasp.ac.uk/browser/dataset/5276/0
Technical mirror used by this spike:
  https://github.com/Leeds-HASP/imd_ppfi_dash_app
"""

import json
from collections.abc import Iterator
from typing import Any

import httpx

HASP_PPFI_DATASET_URL = "https://data.hasp.ac.uk/browser/dataset/5276/0"
HASP_EXPLORER_COMMIT = "63424725c7db67dd3c1546572430a54ea5f9550f"
PPFI_ENGLAND_GEOJSON_URL = (
    "https://raw.githubusercontent.com/Leeds-HASP/imd_ppfi_dash_app/"
    f"{HASP_EXPLORER_COMMIT}/data/ppfi_imd_lsoa_england.geojson"
)


class PpfiClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        url: str = PPFI_ENGLAND_GEOJSON_URL,
    ) -> None:
        self._client = http_client
        self._owns_client = http_client is None
        self._url = url

    async def fetch_geojson(self) -> bytes:
        client = self._client or httpx.AsyncClient(timeout=180.0)
        try:
            response = await client.get(self._url, follow_redirects=True)
            response.raise_for_status()
            return response.content
        finally:
            if self._owns_client:
                await client.aclose()

    @staticmethod
    def iter_rows(content: bytes) -> Iterator[dict[str, Any]]:
        """Yield GeoJSON feature properties, dropping geometry entirely."""
        payload = json.loads(content)
        features = payload.get("features", [])
        if not isinstance(features, list):
            return

        for feature in features:
            if not isinstance(feature, dict):
                continue
            properties = feature.get("properties")
            if isinstance(properties, dict):
                yield properties
