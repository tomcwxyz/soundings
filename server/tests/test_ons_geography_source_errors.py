"""ArcGIS failures must not look like successful empty geography imports."""

from unittest.mock import MagicMock

import httpx
import pytest

from soundings.adapters.ons_geography.endpoints import OgpLayer
from soundings.adapters.ons_geography.geometries_loader import OnsGeographyGeometriesLoader
from soundings.adapters.ons_geography.hierarchy_loader import (
    LookupChain,
    OnsGeographyHierarchyLoader,
)
from soundings.adapters.ons_geography.places_loader import OnsGeographyPlacesLoader


@pytest.mark.parametrize("loader_kind", ["places", "geometries", "hierarchy"])
@pytest.mark.parametrize(
    "payload", [{"error": {"code": 400, "message": "Invalid URL"}}, {"unexpected": []}]
)
async def test_geography_fetch_rejects_error_or_missing_features(
    loader_kind: str, payload: dict[str, object]
) -> None:
    layer = OgpLayer("msoa21", "MSOA21CD", "MSOA21NM", "test_layer")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="ArcGIS"):
            if loader_kind == "places":
                loader = OnsGeographyPlacesLoader(MagicMock(), layers={"msoa21": layer})
                await loader._fetch_page(client, layer, offset=0)
            elif loader_kind == "geometries":
                geometries = OnsGeographyGeometriesLoader(MagicMock(), layers={"msoa21": layer})
                await geometries._fetch_geojson_page(client, layer, offset=0)
            else:
                chain = LookupChain("https://example.test/query", [("msoa21", "MSOA21CD")])
                hierarchy = OnsGeographyHierarchyLoader(MagicMock(), chains=[chain])
                _ = [row async for row in hierarchy._iter_rows(client, chain)]


def test_active_lookup_chains_do_not_invent_political_containment() -> None:
    from soundings.adapters.ons_geography.chains import ALL_CHAINS

    for chain in ALL_CHAINS:
        types = [place_type for place_type, _ in chain.levels]
        assert "westminster_constituency_24" not in types
