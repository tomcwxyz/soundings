"""LSOA → LTLA aggregation for IMD indicators.

Population-weighted average using ONS Mid-Year Estimates as weights. The
aggregation runs after both the MYE loader and the IMD loader have
populated `data.indicator_value`. Idempotent — re-running overwrites the
LTLA rows.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

IMD_INDICATOR_KEYS = (
    "deprivation.imd.score",
    "deprivation.imd.decile",
    "deprivation.imd.income_score",
    "deprivation.imd.employment_score",
    "deprivation.imd.health_score",
    "deprivation.imd.education_score",
    "deprivation.idaci",
    "deprivation.idaopi",
)


AGGREGATE_SQL = text(
    """
    INSERT INTO data.indicator_value (
        place_id, indicator_key, period, value,
        source_id, retrieved_at, loader_run_id, caveats
    )
    SELECT
        ph.parent_id AS place_id,
        imd.indicator_key,
        imd.period,
        SUM(imd.value * COALESCE(pop.value, 1.0))
            / NULLIF(SUM(COALESCE(pop.value, 1.0)), 0) AS value,
        CAST(:source_id AS VARCHAR) AS source_id,
        now() AS retrieved_at,
        NULL AS loader_run_id,
        CASE WHEN BOOL_OR(pop.value IS NOT NULL)
             THEN '["IMD aggregated to LTLA via population-weighted average using MYE."]'::jsonb
             ELSE '["IMD aggregated to LTLA via unweighted average."]'::jsonb
        END AS caveats
    FROM data.indicator_value imd
    JOIN geography.current_place_hierarchy ph ON ph.child_id = imd.place_id
    LEFT JOIN data.indicator_value pop
        ON pop.place_id = imd.place_id
        AND pop.indicator_key = 'population.total'
        AND pop.source_id = 'ons.mid_year_estimates'
    WHERE imd.source_id = :source_id
      AND imd.indicator_key = ANY(:indicator_keys)
      AND ph.parent_id LIKE 'ltla24:%'
    GROUP BY ph.parent_id, imd.indicator_key, imd.period
    ON CONFLICT (place_id, indicator_key, period) DO UPDATE SET
        value = EXCLUDED.value,
        retrieved_at = EXCLUDED.retrieved_at,
        source_id = EXCLUDED.source_id,
        caveats = EXCLUDED.caveats
    """
)


# Mirror the LTLA aggregate into data.trend_point so get_trend can serve a
# cross-edition trend (2019 + 2025 once both aggregations have run). Reads
# from the indicator_value rows the previous statement just wrote — keeps
# the population-weighting logic in one place.
AGGREGATE_TREND_SQL = text(
    """
    INSERT INTO data.trend_point (
        place_id, indicator_key, period, value,
        revised, source_id, retrieved_at
    )
    SELECT
        place_id,
        indicator_key,
        period,
        value,
        false AS revised,
        source_id,
        retrieved_at
    FROM data.indicator_value
    WHERE source_id = :source_id
      AND indicator_key = ANY(:indicator_keys)
      AND place_id LIKE 'ltla24:%'
    ON CONFLICT (place_id, indicator_key, period) DO UPDATE SET
        value = EXCLUDED.value,
        retrieved_at = EXCLUDED.retrieved_at,
        source_id = EXCLUDED.source_id
    """
)


async def aggregate_imd_to_ltla(engine: AsyncEngine, source_id: str = "mhclg.imd2025") -> int:
    """Returns the number of LTLA rows inserted/updated."""
    async with engine.begin() as conn:
        result = await conn.execute(
            AGGREGATE_SQL,
            {"indicator_keys": list(IMD_INDICATOR_KEYS), "source_id": source_id},
        )
        await conn.execute(
            AGGREGATE_TREND_SQL,
            {"indicator_keys": list(IMD_INDICATOR_KEYS), "source_id": source_id},
        )
    return result.rowcount or 0
