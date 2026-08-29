import json

import httpx
import pytest

from soundings.adapters.dwp_statxplore.client import STATXPLORE_BASE, StatXploreClient


async def test_client_posts_to_table_with_apikey_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STATXPLORE_API_KEY", "test-key-12345")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["apikey"] = request.headers.get("apikey")
        captured["body"] = request.read().decode("utf-8")
        return httpx.Response(200, json={"cubes": {}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = StatXploreClient(http_client=http)
        payload = await client.get_table(
            database="str:database:UC_Households",
            measures=["str:count:UC_Households:V_F_UC_HOUSEHOLDS"],
            dimensions=[
                ["str:field:UC_Households:V_F_UC_HOUSEHOLDS:GEOGRAPHY"],
                ["str:field:UC_Households:V_F_UC_HOUSEHOLDS:DATE"],
            ],
            recodes={
                "str:field:UC_Households:V_F_UC_HOUSEHOLDS:GEOGRAPHY": {
                    "map": [["str:value:UC_Households:...:E06000004"]],
                    "total": False,
                }
            },
        )

    assert captured["url"] == f"{STATXPLORE_BASE}/table"
    assert captured["method"] == "POST"
    assert captured["apikey"] == "test-key-12345"
    body_obj = json.loads(captured["body"])  # type: ignore[arg-type]
    assert body_obj["database"] == "str:database:UC_Households"
    assert len(body_obj["dimensions"]) == 2
    assert "recodes" in body_obj
    assert "cubes" in payload


async def test_schema_follows_paginated_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STATXPLORE_API_KEY", "test-key")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers.get("apikey") == "test-key"
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "id": "str:valueset:x",
                    "type": "VALUESET",
                    "children": [{"id": "str:value:x:202601", "type": "VALUE"}],
                },
                headers={
                    "Link": (
                        "<https://stat-xplore.dwp.gov.uk/webapi/rest/v1/"
                        'schema/str%3Avalueset%3Ax?page=2>; rel="next"'
                    )
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "str:valueset:x",
                "type": "VALUESET",
                "children": [{"id": "str:value:x:202602", "type": "VALUE"}],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = StatXploreClient(http_client=http)
        payload = await client.get_schema("str:valueset:x")

    assert calls == 2
    assert [c["id"] for c in payload["children"]] == [
        "str:value:x:202601",
        "str:value:x:202602",
    ]


async def test_client_omits_recodes_when_not_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STATXPLORE_API_KEY", "test-key")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode("utf-8")
        return httpx.Response(200, json={"cubes": {}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = StatXploreClient(http_client=http)
        await client.get_table(
            database="str:database:X",
            measures=["m1"],
            dimensions=[["d1"]],
        )

    body_obj = json.loads(captured["body"])
    assert "recodes" not in body_obj


async def test_client_raises_when_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STATXPLORE_API_KEY", raising=False)
    client = StatXploreClient()
    with pytest.raises(RuntimeError, match="STATXPLORE_API_KEY"):
        await client.get_table(
            database="str:database:X",
            measures=["m"],
            dimensions=[["d"]],
        )


async def test_explicit_api_key_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STATXPLORE_API_KEY", "env-key")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["apikey"] = request.headers.get("apikey", "")
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = StatXploreClient(http_client=http, api_key="ctor-key")
        await client.get_table(database="x", measures=["m"], dimensions=[["d"]])
    assert captured["apikey"] == "ctor-key"
