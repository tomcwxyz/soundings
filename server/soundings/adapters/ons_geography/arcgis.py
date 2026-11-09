"""Validate ArcGIS feature responses, which can carry errors under HTTP 200."""

from typing import Any


def feature_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("ArcGIS response is not an object")
    if "error" in payload:
        raise ValueError(f"ArcGIS source error: {payload['error']}")
    features = payload.get("features")
    if not isinstance(features, list):
        raise ValueError("ArcGIS response is missing a feature list")
    if not all(isinstance(feature, dict) for feature in features):
        raise ValueError("ArcGIS response contains an invalid feature")
    return features
