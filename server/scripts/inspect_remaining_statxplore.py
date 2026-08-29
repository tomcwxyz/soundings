"""Temporary probe for the remaining Stat-Xplore mappings."""

import asyncio
import json
import os
from urllib.parse import quote

import httpx

BASE = "https://stat-xplore.dwp.gov.uk/webapi/rest/v1/schema"
TERMS = (
    "claimant",
    "low income",
    "child",
    "poverty",
    "geography",
    "local authority",
    "date",
    "quarter",
    "year",
)


def brief(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "label": item.get("label"),
        "type": item.get("type"),
    }


def relevant(item: dict) -> bool:
    haystack = f"{item.get('id', '')} {item.get('label', '')}".lower()
    return any(term in haystack for term in TERMS)


async def get(client: httpx.AsyncClient, schema_id: str | None = None) -> dict:
    url = BASE if schema_id is None else f"{BASE}/{quote(schema_id, safe='')}"
    response = await client.get(
        url,
        headers={"APIKey": os.environ["STATXPLORE_API_KEY"]},
    )
    print("GET", schema_id or "<root>", response.status_code)
    if response.status_code >= 400:
        print(response.text[:1000])
        return {}
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


async def inspect(client: httpx.AsyncClient, schema_id: str, depth: int = 0) -> None:
    payload = await get(client, schema_id)
    children = [c for c in payload.get("children", []) if isinstance(c, dict)]
    selected = [c for c in children if relevant(c)]
    print("NODE", json.dumps(brief(payload)))
    print("RELEVANT_CHILDREN", json.dumps([brief(c) for c in selected], indent=2))
    if depth >= 2:
        return
    for child in selected:
        child_id = child.get("id")
        if isinstance(child_id, str):
            await inspect(client, child_id, depth + 1)


async def main() -> None:
    async with httpx.AsyncClient(timeout=90.0) as client:
        root = await get(client)
        root_children = [c for c in root.get("children", []) if isinstance(c, dict)]
        matches = [c for c in root_children if relevant(c)]
        print("ROOT_MATCHES", json.dumps([brief(c) for c in matches], indent=2))

        candidates = {
            "str:database:cc_quarterly",
            "str:database:children_in_low_income_families_ahc",
        }
        candidates.update(
            c["id"]
            for c in matches
            if isinstance(c.get("id"), str) and c.get("type") == "DATABASE"
        )
        for candidate in sorted(candidates):
            await inspect(client, candidate)


if __name__ == "__main__":
    asyncio.run(main())
