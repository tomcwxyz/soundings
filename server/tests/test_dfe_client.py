"""Unit tests for DfeExploreClient (mock transport)."""

import httpx

from soundings.adapters.dfe_explore.client import DFE_EXPLORE_BASE, DfeExploreClient


async def test_client_posts_to_query_with_indicators_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["body"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "paging": {"page": 1, "pageSize": 1000, "totalResults": 1, "totalPages": 1},
                "results": [
                    {
                        "timePeriod": {"code": "AY", "period": "2022/2023"},
                        "locations": {"LA": "loc-stockton"},
                        "filters": {},
                        "values": {"ind-fsm": "0.215"},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = DfeExploreClient(http_client=http)
        payload = await client.query_dataset(
            data_set_id="ds-abc",
            indicators=["ind-fsm"],
            criteria={
                "locations": {"in": [{"level": "LA", "code": "E06000004"}]},
                "timePeriods": {"gte": {"period": "2022/2023", "code": "AY"}},
            },
        )

    assert str(captured["url"]).startswith(f"{DFE_EXPLORE_BASE}/data-sets/ds-abc/query")
    assert captured["method"] == "POST"

    import json as _json

    body_obj = _json.loads(captured["body"])  # type: ignore[arg-type]
    assert body_obj["indicators"] == ["ind-fsm"]
    assert "criteria" in body_obj
    assert body_obj["criteria"]["locations"]["in"][0]["code"] == "E06000004"

    assert payload["results"][0]["values"]["ind-fsm"] == "0.215"


async def test_client_omits_criteria_when_not_supplied() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode("utf-8")
        return httpx.Response(200, json={"paging": {"page": 1, "pageSize": 1000}, "results": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = DfeExploreClient(http_client=http)
        await client.query_dataset(data_set_id="ds-x", indicators=["i1"])

    import json as _json

    body_obj = _json.loads(captured["body"])
    assert "criteria" not in body_obj
    assert body_obj["indicators"] == ["i1"]


async def test_client_passes_pagination_in_post_body() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode("utf-8")
        return httpx.Response(200, json={"paging": {}, "results": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = DfeExploreClient(http_client=http)
        await client.query_dataset(data_set_id="ds-x", indicators=["i1"], page=3, page_size=50)

    import json as _json

    body_obj = _json.loads(captured["body"])
    assert body_obj["page"] == 3
    assert body_obj["pageSize"] == 50
    assert "page=" not in captured["url"]
    assert "pageSize=" not in captured["url"]
