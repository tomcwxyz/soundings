"""Temporary probe for the remaining Stat-Xplore mappings."""

import asyncio
import json
import os
from urllib.parse import quote

import httpx

BASE = "https://stat-xplore.dwp.gov.uk/webapi/rest/v1/schema"
TARGET_FOLDERS = ("str:folder:facc", "str:folder:fcilif")
TERMS = (
    "geography",
    "local authority",
    "date",
    "quarter",
    "year",
    "count",
    "rate",
    "percentage",
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


async def get(client: httpx.AsyncClient, schema_id: str) -> dict:
    url = f"{BASE}/{quote(schema_id, safe='')}"
    response = await client.get(
        url,
        headers={"APIKey": os.environ["STATXPLORE_API_KEY"]},
    )
    print("GET", schema_id, response.status_code)
    if response.status_code >= 400:
        print(response.text[:1000])
        return {}
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


async def inspect_database(client: httpx.AsyncClient, database_id: str) -> None:
    payload = await get(client, database_id)
    children = [c for c in payload.get("children", []) if isinstance(c, dict)]
    print("DATABASE", json.dumps(brief(payload)))
    print("DATABASE_RELEVANT", json.dumps([brief(c) for c in children if relevant(c)], indent=2))

    for child in [c for c in children if relevant(c)]:
        child_id = child.get("id")
        if not isinstance(child_id, str):
            continue
        detail = await get(client, child_id)
        grandchildren = [c for c in detail.get("children", []) if isinstance(c, dict)]
        print("CHILD", json.dumps(brief(detail)))
        print("CHILDREN", json.dumps([brief(c) for c in grandchildren], indent=2))


async def main() -> None:
    async with httpx.AsyncClient(timeout=90.0) as client:
        for folder_id in TARGET_FOLDERS:
            folder = await get(client, folder_id)
            children = [c for c in folder.get("children", []) if isinstance(c, dict)]
            print("FOLDER", json.dumps(brief(folder)))
            print("FOLDER_CHILDREN", json.dumps([brief(c) for c in children], indent=2))
            for child in children:
                child_id = child.get("id")
                if child.get("type") == "DATABASE" and isinstance(child_id, str):
                    await inspect_database(client, child_id)


if __name__ == "__main__":
    asyncio.run(main())
