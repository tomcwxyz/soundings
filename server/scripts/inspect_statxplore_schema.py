"""Temporary authenticated schema probe for Stat-Xplore mapping repair."""

import asyncio
import json
import os
from urllib.parse import quote

import httpx

BASE = "https://stat-xplore.dwp.gov.uk/webapi/rest/v1/schema"
KEYWORDS = ("geog", "date", "month", "local", "authority", "council", "district", "unitary")


async def fetch(client: httpx.AsyncClient, schema_id: str) -> dict:
    url = f"{BASE}/{quote(schema_id, safe='')}"
    response = await client.get(url, headers={"APIKey": os.environ["STATXPLORE_API_KEY"]})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected schema payload for {schema_id}")
    return payload


def interesting(item: dict) -> bool:
    label = str(item.get("label", "")).lower()
    ident = str(item.get("id", "")).lower()
    return any(k in label or k in ident for k in KEYWORDS)


def summary(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "label": item.get("label"),
        "type": item.get("type"),
    }


async def main() -> None:
    async with httpx.AsyncClient(timeout=60.0) as client:
        root = await fetch(client, "str:database:UC_Monthly")
        children = [c for c in root.get("children", []) if isinstance(c, dict)]
        print("ROOT_CHILDREN")
        print(json.dumps([summary(c) for c in children], indent=2))

        first = [c for c in children if interesting(c)]
        print("INTERESTING_ROOT_CHILDREN")
        print(json.dumps([summary(c) for c in first], indent=2))

        for child in first:
            child_id = child.get("id")
            if not isinstance(child_id, str):
                continue
            try:
                detail = await fetch(client, child_id)
            except httpx.HTTPStatusError as exc:
                print(f"DETAIL_ERROR {child_id}: {exc.response.status_code} {exc.response.text[:500]}")
                continue
            grandchildren = [c for c in detail.get("children", []) if isinstance(c, dict)]
            print(f"CHILDREN_OF {child_id}")
            print(json.dumps([summary(c) for c in grandchildren], indent=2))

            for grandchild in [c for c in grandchildren if interesting(c)]:
                gid = grandchild.get("id")
                if not isinstance(gid, str):
                    continue
                try:
                    gdetail = await fetch(client, gid)
                except httpx.HTTPStatusError as exc:
                    print(f"DETAIL_ERROR {gid}: {exc.response.status_code} {exc.response.text[:500]}")
                    continue
                great = [c for c in gdetail.get("children", []) if isinstance(c, dict)]
                if great:
                    print(f"CHILDREN_OF {gid}")
                    print(json.dumps([summary(c) for c in great[:200]], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
