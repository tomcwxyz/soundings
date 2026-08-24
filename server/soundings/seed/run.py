"""CLI: `python -m soundings.seed.run --full | --light | --refresh-trends`.

Wraps the ons.geography loaders (places, hierarchy, geometries, code change)
and writes a `data.loader_run` row per loader. Intended to be run inside the
docker compose `server` container via `make seed` / `make seed-light` /
`make refresh-trends`.

`--light` skips the heaviest layers (LSOA + MSOA) so a Mac mini dev box can
get a usable spine in ~5 minutes for testing.

`--refresh-trends` re-runs only the loaders that write `data.trend_point`
rows (MYE + both IMD editions and their LTLA aggregations). Useful for
refreshing sparkline data without touching the geography spine or Census.
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.base import LoaderResult
from soundings.adapters.mhclg_imd2025.aggregation import aggregate_imd_to_ltla
from soundings.adapters.mhclg_imd2025.loader import MhclgImd2019Loader, MhclgImd2025Loader
from soundings.adapters.ons_census2021.loader import OnsCensus2021Loader
from soundings.adapters.ons_geography.chains import ALL_CHAINS
from soundings.adapters.ons_geography.code_change_loader import (
    OnsGeographyCodeChangeLoader,
)
from soundings.adapters.ons_geography.endpoints import BOUNDARY_LAYERS
from soundings.adapters.ons_geography.geometries_loader import (
    OnsGeographyGeometriesLoader,
)
from soundings.adapters.ons_geography.hierarchy_loader import (
    OnsGeographyHierarchyLoader,
)
from soundings.adapters.ons_geography.places_loader import OnsGeographyPlacesLoader
from soundings.adapters.ons_mid_year_estimates.loader import OnsMidYearEstimatesLoader
from soundings.adapters.ons_nspl.loader import NsplLoader
from soundings.db.engine import get_engine
from soundings.seed.themes import seed_themes

LIGHT_LAYERS = {"ltla24", "utla24", "region", "country", "westminster_constituency_24", "ward24"}


async def _run_loader(
    engine: AsyncEngine,
    source_id: str,
    name: str,
    coro: Coroutine[Any, Any, LoaderResult],
) -> LoaderResult:
    run_id = uuid.uuid4()
    started = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO data.loader_run "
                "(id, source_id, started_at, status, rows_written) "
                "VALUES (:id, :sid, :st, 'running', 0)"
            ),
            {"id": run_id, "sid": source_id, "st": started},
        )
    try:
        result: LoaderResult = await coro
        finished = datetime.now(tz=UTC)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE data.loader_run "
                    "SET status='ok', finished_at=:f, rows_written=:r, notes=:n "
                    "WHERE id=:id"
                ),
                {
                    "f": finished,
                    "r": result.rows_written,
                    "n": result.notes,
                    "id": run_id,
                },
            )
        print(f"[seed] {name}: {result.rows_written} rows ({result.notes or 'ok'})")
        return result
    except Exception as exc:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE data.loader_run "
                    "SET status='failed', finished_at=:f, notes=:n WHERE id=:id"
                ),
                {
                    "f": datetime.now(tz=UTC),
                    "n": f"{exc.__class__.__name__}: {exc}",
                    "id": run_id,
                },
            )
        raise


async def _seed(*, full: bool) -> None:
    engine = get_engine()

    # Theme vocabulary must exist before any observation-dependent seeds
    # (data.observation.theme FK → catalogue.theme.key).
    await seed_themes(engine)

    layers = (
        BOUNDARY_LAYERS if full else {k: v for k, v in BOUNDARY_LAYERS.items() if k in LIGHT_LAYERS}
    )

    places = OnsGeographyPlacesLoader(engine, layers=layers)
    await _run_loader(engine, "ons.geography", "places", places.load())

    hierarchy = OnsGeographyHierarchyLoader(engine, chains=ALL_CHAINS)
    await _run_loader(engine, "ons.geography", "hierarchy", hierarchy.load())

    geoms = OnsGeographyGeometriesLoader(engine, layers=layers)
    await _run_loader(engine, "ons.geography", "geometries", geoms.load())

    chd = OnsGeographyCodeChangeLoader(engine)
    await _run_loader(engine, "ons.geography", "code_change", chd.load())

    # NSPL populates geography.postcode (postcode -> statutory geographies) so
    # postcode-based loaders resolve locally instead of via postcodes.io.
    # Full-only: ~2.7M postcodes. Needs places + hierarchy loaded above (FK
    # guard + utla24 derivation).
    if full:
        nspl = NsplLoader(engine)
        await _run_loader(engine, "ons.nspl", "nspl", nspl.load())

    # Indicator data — Phase 1 sources. `--light` filters to the dev LTLA
    # so a fresh box doesn't burn through Nomis rate limits.
    light_filter = ["ltla24:E06000004"] if not full else None
    mye = OnsMidYearEstimatesLoader(engine, place_filter=light_filter)
    await _run_loader(engine, "ons.mid_year_estimates", "mye", mye.load())

    census = OnsCensus2021Loader(engine, place_filter=light_filter)
    await _run_loader(engine, "ons.census2021", "census", census.load())

    # IMD must run after MYE: the LSOA→LTLA aggregation is population-weighted
    # using mid-year estimate values.
    imd2025 = MhclgImd2025Loader(engine)
    await _run_loader(engine, "mhclg.imd2025", "imd2025", imd2025.load())
    agg2025 = await aggregate_imd_to_ltla(engine, source_id="mhclg.imd2025")
    print(f"[seed] imd2025_aggregation: {agg2025} LTLA rows")

    imd2019 = MhclgImd2019Loader(engine)
    await _run_loader(engine, "mhclg.imd2019", "imd2019", imd2019.load())
    agg2019 = await aggregate_imd_to_ltla(engine, source_id="mhclg.imd2019")
    print(f"[seed] imd2019_aggregation: {agg2019} LTLA rows")


async def _refresh_trends() -> None:
    """Re-run only the loaders that write `data.trend_point` rows.

    Assumes the geography spine and Census data are already in place. Order
    matches `_seed`: MYE first so the IMD LTLA aggregation has population
    weights to use. MYE runs for every place currently in `geography.place`
    — passing `--light` to the initial seed determines that scope, not this
    refresh.
    """
    engine = get_engine()

    mye = OnsMidYearEstimatesLoader(engine)
    await _run_loader(engine, "ons.mid_year_estimates", "mye", mye.load())

    imd2025 = MhclgImd2025Loader(engine)
    await _run_loader(engine, "mhclg.imd2025", "imd2025", imd2025.load())
    agg2025 = await aggregate_imd_to_ltla(engine, source_id="mhclg.imd2025")
    print(f"[seed] imd2025_aggregation: {agg2025} LTLA rows")

    imd2019 = MhclgImd2019Loader(engine)
    await _run_loader(engine, "mhclg.imd2019", "imd2019", imd2019.load())
    agg2019 = await aggregate_imd_to_ltla(engine, source_id="mhclg.imd2019")
    print(f"[seed] imd2019_aggregation: {agg2019} LTLA rows")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="soundings-seed")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--full", action="store_true")
    mode.add_argument("--light", action="store_true")
    mode.add_argument(
        "--refresh-trends",
        action="store_true",
        help="Re-run only the trend-writing loaders (MYE + both IMD editions).",
    )
    args = parser.parse_args(argv)
    if args.refresh_trends:
        asyncio.run(_refresh_trends())
    else:
        asyncio.run(_seed(full=args.full))
    return 0


if __name__ == "__main__":
    sys.exit(main())
