"""Unit tests for the PPFI client."""

import json

from soundings.adapters.ppfi.client import PpfiClient


def test_iter_rows_yields_properties_and_ignores_geometry() -> None:
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "LSOA21CD": "E01000001",
                    "pp_dec_combined": 1,
                },
                "geometry": {"type": "Polygon", "coordinates": []},
            },
            {"type": "Feature", "properties": None, "geometry": None},
        ],
    }

    rows = list(PpfiClient.iter_rows(json.dumps(payload).encode()))

    assert rows == [{"LSOA21CD": "E01000001", "pp_dec_combined": 1}]
