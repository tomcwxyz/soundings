"""Offline shape validation for Scottish Government ArcGIS DZ22 GeoJSON."""

from soundings.adapters.gov_scot_geography.geometries_loader import feature_to_update


def _feature() -> dict[str, object]:
    return {
        "type": "Feature",
        "properties": {"DZCode": "S01000001", "DZName": "Aberdeen test zone"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-2.10, 57.15], [-2.11, 57.15], [-2.10, 57.16], [-2.10, 57.15]]],
        },
    }


def test_extract_valid_data_zone_geometry() -> None:
    row = feature_to_update(_feature())
    assert row is not None
    assert row["id"] == "dz22:S01000001"
    assert row["name"] == "Aberdeen test zone"
    assert '"Polygon"' in row["geometry"]


def test_reject_non_scottish_id() -> None:
    feature = _feature()
    feature["properties"] = {"DZCode": "E01000001"}
    assert feature_to_update(feature) is None


def test_reject_projected_british_grid_geometry() -> None:
    feature = _feature()
    feature["geometry"] = {
        "type": "Polygon",
        "coordinates": [[[390000, 800000], [390010, 800000], [390000, 800010], [390000, 800000]]],
    }
    assert feature_to_update(feature) is None


def test_reject_non_polygon_or_unexpected_shape() -> None:
    feature = _feature()
    feature["geometry"] = {"type": "Point", "coordinates": [-2.1, 57.1]}
    assert feature_to_update(feature) is None
