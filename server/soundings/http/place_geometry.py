"""GET /v1/place/{place_id}/geometry and GET /v1/place/{place_id}/peers/geometry.

Returns simplified GeoJSON for a single place or its peers (same `type`,
excluding the place itself). Geometries are simplified with
ST_Simplify(geom, 0.005) and serialised via ST_AsGeoJSON.

Peer geometries optionally join `data.indicator_value` to attach the
indicator value and percentile for a given indicator/period.

GET /v1/place/{place_id}/amenities/geometry merges OSM amenity point
features across multiple infrastructure indicators into a single
FeatureCollection. Per-indicator failures degrade gracefully.
"""

import json

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text

router = APIRouter(prefix="/v1")


@router.get("/place/{place_id}/geometry")
async def get_place_geometry(request: Request, place_id: str) -> dict[str, object]:
    engine = request.app.state.engine
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT
                        p.id,
                        p.name,
                        p.type,
                        ST_AsGeoJSON(ST_Simplify(p.geom, 0.005)) AS geojson
                    FROM geography.place p
                    WHERE p.id = :place_id
                    """
                ),
                {"place_id": place_id},
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="place not found")
    geometry = _parse_geojson(row.geojson)
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "id": row.id,
            "name": row.name,
            "type": row.type,
        },
    }


def _parse_geojson(geojson_str: str | None) -> dict[str, object] | None:
    """ST_AsGeoJSON returns a text JSON string; parse it, or None if NULL."""
    if geojson_str is None:
        return None
    parsed: dict[str, object] = json.loads(geojson_str)
    return parsed


@router.get("/place/{place_id}/peers/geometry")
async def get_peer_geometry(
    request: Request,
    place_id: str,
    indicator: str | None = Query(default=None),
    period: str | None = Query(default=None),
) -> dict[str, object]:
    """GeoJSON FeatureCollection of peer places (same `type`, excluding
    the given place). Each feature's properties carry {id, name, value,
    percentile} where value/percentile come from a left join on
    `data.indicator_value` for the given indicator/period."""
    engine = request.app.state.engine
    async with engine.connect() as conn:
        place_row = (
            await conn.execute(
                text("SELECT type FROM geography.place WHERE id = :place_id"),
                {"place_id": place_id},
            )
        ).first()
        if place_row is None:
            raise HTTPException(status_code=404, detail="place not found")
        place_type = place_row.type

        rows = (
            await conn.execute(
                text(
                    """
                    WITH peer_values AS (
                        SELECT
                            p.id,
                            p.name,
                            iv.value
                        FROM geography.place p
                        LEFT JOIN LATERAL (
                            SELECT v.value
                            FROM data.indicator_value v
                            WHERE v.place_id = p.id
                              AND v.indicator_key = :indicator
                              AND (COALESCE(:period, v.period) = v.period)
                            ORDER BY v.period DESC
                            LIMIT 1
                        ) iv ON TRUE
                        WHERE p.type = :place_type
                            AND p.id <> :place_id
                    ),
                    ranked AS (
                        SELECT
                            pv.*,
                            RANK() OVER (
                                ORDER BY pv.value DESC NULLS LAST
                            ) AS rank,
                            COUNT(pv.value) OVER () AS n_with_values
                        FROM peer_values pv
                    )
                    SELECT
                        r.id,
                        r.name,
                        r.value,
                        r.rank,
                        r.n_with_values,
                        ST_AsGeoJSON(ST_Simplify(p.geom, 0.005)) AS geojson
                    FROM ranked r
                    JOIN geography.place p ON p.id = r.id
                    """
                ),
                {
                    "place_id": place_id,
                    "place_type": place_type,
                    "indicator": indicator,
                    "period": period,
                },
            )
        ).all()

    features: list[dict[str, object]] = []
    for r in rows:
        geometry = _parse_geojson(r.geojson)
        percentile = _percentile_from_rank(r.rank, r.n_with_values)
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "id": r.id,
                    "name": r.name,
                    "value": float(r.value) if r.value is not None else None,
                    "percentile": percentile,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _percentile_from_rank(rank: int | None, n: int) -> float | None:
    """`(n - rank) / (n - 1) * 100`. Top = 100, bottom = 0.
    Undefined when n <= 1 or value is missing."""
    if rank is None or n is None or n <= 1 or rank > n:
        return None
    return (n - rank) / (n - 1) * 100.0


@router.get("/place/{place_id}/children/geometry")
async def get_children_geometry(
    request: Request,
    place_id: str,
    indicator: str = Query(...),
    period: str | None = Query(default=None),
    child_type: str = Query(default="lsoa21"),
) -> dict[str, object]:
    """FeatureCollection of a place's current sub-areas (default LSOAs)
    coloured by an indicator. A LATERAL join picks the latest period per child
    when `period` is omitted. Children without a value are excluded so the
    caller can detect 'no sub-area data' and fall back to peer mode."""
    engine = request.app.state.engine
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT c.id, c.name,
                           ST_AsGeoJSON(ST_Simplify(c.geom, 0.005)) AS geojson,
                           iv.value
                    FROM geography.current_place_hierarchy h
                    JOIN geography.place c ON c.id = h.child_id
                    LEFT JOIN LATERAL (
                        SELECT v.value
                        FROM data.indicator_value v
                        WHERE v.place_id = c.id
                          AND v.indicator_key = :indicator
                          AND (COALESCE(:period, v.period) = v.period)
                        ORDER BY v.period DESC
                        LIMIT 1
                    ) iv ON TRUE
                    WHERE h.parent_id = :place_id
                      AND c.type = :child_type
                      AND c.geom IS NOT NULL
                    """
                ),
                {
                    "place_id": place_id,
                    "indicator": indicator,
                    "period": period,
                    "child_type": child_type,
                },
            )
        ).all()

    features: list[dict[str, object]] = []
    for r in rows:
        if r.value is None:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": _parse_geojson(r.geojson),
                "properties": {"id": r.id, "name": r.name, "value": float(r.value)},
            }
        )
    return {"type": "FeatureCollection", "features": features}


@router.get("/geographies/{place_type}/geometry")
async def get_geographies_geometry(
    request: Request,
    place_type: str,
    indicator: str = Query(...),
    period: str | None = Query(default=None),
    large: bool = Query(default=False),
) -> dict[str, object]:
    """GeoJSON FeatureCollection of ALL places of `place_type`, coloured by an
    indicator (value + percentile). The national counterpart to
    `peers/geometry` (which is relative to one place). Latest period per place
    when `period` is omitted. Large layers (lsoa21) require `large=true`."""
    if place_type == "lsoa21" and not large:
        raise HTTPException(
            status_code=422,
            detail="lsoa21 is a large layer; pass large=true to confirm",
        )
    engine = request.app.state.engine
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    WITH area_values AS (
                        SELECT p.id, p.name, iv.value
                        FROM geography.place p
                        LEFT JOIN LATERAL (
                            SELECT v.value
                            FROM data.indicator_value v
                            WHERE v.place_id = p.id
                              AND v.indicator_key = :indicator
                              AND (COALESCE(:period, v.period) = v.period)
                            ORDER BY v.period DESC
                            LIMIT 1
                        ) iv ON TRUE
                        WHERE p.type = :place_type
                    ),
                    ranked AS (
                        SELECT av.*,
                               RANK() OVER (ORDER BY av.value DESC NULLS LAST) AS rank,
                               COUNT(av.value) OVER () AS n_with_values
                        FROM area_values av
                    )
                    SELECT r.id, r.name, r.value, r.rank, r.n_with_values,
                           ST_AsGeoJSON(ST_Simplify(p.geom, 0.005)) AS geojson
                    FROM ranked r
                    JOIN geography.place p ON p.id = r.id
                    """
                ),
                {"place_type": place_type, "indicator": indicator, "period": period},
            )
        ).all()

    features: list[dict[str, object]] = []
    for r in rows:
        features.append(
            {
                "type": "Feature",
                "geometry": _parse_geojson(r.geojson),
                "properties": {
                    "id": r.id,
                    "name": r.name,
                    "value": float(r.value) if r.value is not None else None,
                    "percentile": _percentile_from_rank(r.rank, r.n_with_values),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


@router.get("/place/{place_id}/amenities/geometry")
async def get_amenities_geometry(
    request: Request,
    place_id: str,
    indicators: str = Query(..., description="comma-separated indicator keys"),
) -> dict[str, object]:
    """Merged FeatureCollection of amenity point locations. Each indicator is
    routed to the adapter that owns it (per its catalogue source_id), so food
    banks come from Give Food while schools/GPs come from OSM. Per-indicator
    failures degrade to a partial collection rather than failing the request."""
    keys = [k.strip() for k in indicators.split(",") if k.strip()][:6]
    engine = request.app.state.engine
    registry = request.app.state.adapter_registry

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT key, source_id FROM catalogue.indicator WHERE key = ANY(:keys)"),
                {"keys": keys},
            )
        ).all()
    source_by_key = {r.key: r.source_id for r in rows}

    features: list[dict[str, object]] = []
    errors: list[str] = []
    for key in keys:
        source_id = source_by_key.get(key)
        if source_id is None:
            errors.append(f"{key}: unknown indicator")
            continue
        try:
            adapter = registry.adapter_for_source(source_id)
            fc = await adapter.amenity_locations(key, place_id)
        except Exception as exc:
            errors.append(f"{key}: {exc.__class__.__name__}")
            continue
        if fc:
            features.extend(fc.get("features", []))

    result: dict[str, object] = {"type": "FeatureCollection", "features": features}
    if errors:
        result["errors"] = errors
    return result


@router.get("/place/{place_id}/organisations/geometry")
async def get_organisations_geometry(
    request: Request,
    place_id: str,
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, object]:
    """GeoJSON FeatureCollection of charity registered-address locations
    within a place. Each feature is a Point with properties: id, name,
    income, cause, register_url.

    Only charities with a postcode that resolves to a lat/lon are included.
    Charities that operate here but are registered elsewhere are NOT
    included (no coordinates for them).
    """
    engine = request.app.state.engine
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT o.id, o.name,
                           (o.raw->>'latest_income')::numeric AS income,
                           o.raw->>'postcode' AS postcode,
                           o.classification,
                           p.latitude, p.longitude
                    FROM data.organisation o
                    JOIN geography.postcode p
                        ON p.postcode = REPLACE(o.raw->>'postcode', ' ', '')
                    WHERE o.registered_address_place_id = :pid
                      AND p.latitude IS NOT NULL
                      AND p.longitude IS NOT NULL
                    ORDER BY (o.raw->>'latest_income')::numeric DESC NULLS LAST
                    LIMIT :limit
                    """
                ),
                {"pid": place_id, "limit": limit},
            )
        ).all()

    features: list[dict[str, object]] = []
    for r in rows:
        reg_no = r.id.split(":", 1)[1] if ":" in r.id else None
        register_url: str | None = None
        if reg_no:
            register_url = (
                "https://register-of-charities.charitycommission.gov.uk/"
                f"charity-search-/charity-details/{reg_no}"
            )
        classification = list(r.classification or [])
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(r.longitude), float(r.latitude)],
                },
                "properties": {
                    "id": r.id,
                    "name": r.name,
                    "income": float(r.income) if r.income is not None else None,
                    "cause": classification[0] if classification else None,
                    "register_url": register_url,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}
