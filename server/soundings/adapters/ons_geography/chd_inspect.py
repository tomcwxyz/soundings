"""Read-only CLI for inspecting ONS Code History Database archives."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx

from soundings.adapters.ons_geography.chd_archive import inspect_chd_archive
from soundings.adapters.ons_geography.chd_hierarchy_loader import CHD_CURRENT_ARCGIS_URL


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


async def inspect_remote_chd(
    url: str,
    *,
    sample_size: int = 2,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Download one CHD zip and return a serialisable, non-mutating inventory."""
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=120.0, follow_redirects=True)
    try:
        response = await client.get(url)
        response.raise_for_status()
        inventory = inspect_chd_archive(response.content, sample_size=sample_size)
    finally:
        if owns_client:
            await client.aclose()

    return {
        "source_url": url,
        "table_count": len(inventory.tables),
        "history_table_count": len(inventory.history_tables),
        "change_history_table_count": len(inventory.change_history_tables),
        "hierarchy_table_count": len(inventory.hierarchy_tables),
        "tables": inventory.summary(),
    }


async def _run(url: str, sample_size: int) -> int:
    try:
        report = await inspect_remote_chd(url, sample_size=sample_size)
    except Exception as exc:
        print(f"[chd-inspect] {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="soundings-chd-inspect",
        description="Inspect an ONS Code History Database zip without writing data.",
    )
    parser.add_argument(
        "--url",
        default=CHD_CURRENT_ARCGIS_URL,
        help="CHD zip URL (defaults to the current ONS ArcGIS CSV collection)",
    )
    parser.add_argument(
        "--sample-size",
        type=_non_negative_int,
        default=2,
        metavar="N",
        help="sample rows to include per CSV table (default: 2)",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.url, args.sample_size))


if __name__ == "__main__":
    sys.exit(main())
