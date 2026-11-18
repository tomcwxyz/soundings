"""Give Food client regression tests for both documented location formats."""

import httpx
import pytest

from soundings.adapters.givefood.client import (
    LOCATIONS_URL,
    MIRROR_URL,
    GiveFoodClient,
    GiveFoodUnavailableError,
    _parse_locations,
)


def _locations() -> list[dict]:
    return [
        {
            "id": "distribution-site-1",
            "name": "Leeds Central",
            "foodbank": {"name": "Leeds Foodbank", "urls": {"html": "https://example.org/food"}},
            "lat_lng": "53.8013,-1.5481",
            "postcode": "LS1 1AA",
        },
        {
            "id": "distribution-site-2",
            "name": "Leeds North",
            "foodbank": {"name": "Leeds Foodbank"},
            "lat_lng": "53.8448,-1.5414",
            "postcode": "LS7 1AA",
        },
    ]


async def test_locations_api_preserves_separate_distribution_sites() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_locations())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        rows = await GiveFoodClient(http).fetch_foodbanks()

    assert len(rows) == 2  # one parent organisation, two distribution sites
    assert rows[0]["name"] == "Leeds Central"
    assert rows[0]["org"] == "Leeds Foodbank"
    assert rows[0]["lat"] == 53.8013
    assert rows[0]["lng"] == -1.5481
    assert rows[0]["url"] == "https://example.org/food"
    assert rows[1]["name"] == "Leeds North"
    assert len(requests) == 1
    assert str(requests[0].url) == LOCATIONS_URL
    assert "Soundings" in requests[0].headers["user-agent"]


async def test_api_failure_uses_location_level_geojson_mirror() -> None:
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        if str(request.url) == LOCATIONS_URL:
            return httpx.Response(404, text="Not Found")
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [-1.5481, 53.8013]},
                        "properties": {
                            "name": "Leeds Central",
                            "foodbank": {"name": "Leeds Foodbank"},
                            "postcode": "LS1 1AA",
                        },
                    },
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [-1.5414, 53.8448]},
                        "properties": {
                            "name": "Leeds North",
                            "foodbank": {"name": "Leeds Foodbank"},
                        },
                    },
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        rows = await GiveFoodClient(http).fetch_foodbanks()

    assert called == [LOCATIONS_URL, MIRROR_URL]
    assert len(rows) == 2
    assert rows[0]["lat"] == 53.8013
    assert rows[0]["lng"] == -1.5481
    assert rows[1]["org"] == "Leeds Foodbank"


async def test_wrapped_api_response_supported() -> None:
    rows = _parse_locations({"results": _locations()})
    assert len(rows) == 2


async def test_both_sources_fail_with_actionable_statuses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        status = 404 if str(request.url) == LOCATIONS_URL else 503
        return httpx.Response(status, text="unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(GiveFoodUnavailableError, match="api: HTTP 404") as error:
            await GiveFoodClient(http).fetch_foodbanks()
    assert "geojson_mirror: HTTP 503" in str(error.value)


async def test_invalid_schema_never_becomes_empty_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"unexpected": "shape"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(GiveFoodUnavailableError, match="invalid response"):
            await GiveFoodClient(http).fetch_foodbanks()


async def test_unusable_coordinates_do_not_get_cached_as_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=[{"name": "Somewhere", "lat_lng": "bad"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(GiveFoodUnavailableError, match="no usable location coordinates"):
            await GiveFoodClient(http).fetch_foodbanks()


async def test_legacy_dump_style_rows_still_parse() -> None:
    rows = _parse_locations(
        [
            {
                "organisation_name": "County Durham",
                "location_name": "Annfield Plain",
                "lat_lng": "54.8588523,-1.7377999",
                "postcode": "DH9 7SY",
                "lsoa": "E01020700",
            }
        ]
    )
    assert rows[0]["name"] == "Annfield Plain"
    assert rows[0]["org"] == "County Durham"
    assert rows[0]["lsoa"] == "E01020700"
    assert rows[0]["lat"] == 54.8588523


async def test_malformed_coordinate_is_not_accepted() -> None:
    rows = _parse_locations(
        [
            {"name": "Valid", "lat_lng": "53.8,-1.5"},
            {"name": "Invalid", "lat_lng": "unknown,unknown"},
        ]
    )
    assert rows[1]["lat"] is None
    assert rows[1]["lng"] is None
