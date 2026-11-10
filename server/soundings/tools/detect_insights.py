"""detect_insights tool — pure-SQL deterministic insight detector.

Surfaces three deterministic signal types for a single place:

1. **extreme_percentile** — the place's indicator value sits at the extreme
   tail of its same-type peer distribution (PERCENT_RANK). Severity is
   ``extreme`` for the outer 5% (<=5 or >=95) and ``notable`` for the outer
   10% (<=10 or >=90).

2. **peer_divergence** — the place's indicator value is more than one
   population standard deviation from the same-type-peer median
   (PERCENTILE_CONT(0.5) + STDDEV_POP).

3. **trend_reversal** — the latest trend point's slope sign differs from
   the prior 3-point average slope (requires >=5 trend points).

All detection is done in pure SQL against ``data.indicator_value`` and
``data.trend_point`` so the detector is deterministic and easy to audit.
"""

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.catalogue.contracts import get_contract_registry
from soundings.orchestration.measure_guard import value_bounds

Severity = Literal["extreme", "notable"]
SignalKind = Literal["extreme_percentile", "peer_divergence", "trend_reversal"]


class DetectInsightsInput(BaseModel):
    place_id: str
    indicator_keys: list[str] | None = None


class InsightSignal(BaseModel):
    indicator_key: str
    severity: Severity
    kind: SignalKind
    evidence_payload: dict[str, object] = Field(default_factory=dict)


class DetectInsightsOutput(BaseModel):
    signals: list[InsightSignal] = Field(default_factory=list)


TOOL_NAME = "detect_insights"
TOOL_DESCRIPTION = (
    "Deterministic, SQL-driven insight detector. Scans a single place's "
    "indicator values and trend points for three signal types — extreme "
    "percentiles, peer divergence, and trend reversals — using pure SQL "
    "against the loaded data tables."
)


def tool_spec() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": DetectInsightsInput.model_json_schema(),
        "output_schema": DetectInsightsOutput.model_json_schema(),
    }


async def detect_insights(
    input: DetectInsightsInput,
    engine: AsyncEngine,
) -> DetectInsightsOutput:
    """Detect deterministic insight signals for a single place.

    The query fan-out is per-indicator (one round-trip per indicator key),
    keeping the detector transparent: every signal is backed by an
    auditable SQL result. When ``indicator_keys`` is ``None`` every
    indicator value recorded for the place is considered.
    """
    indicator_keys = input.indicator_keys
    if indicator_keys is None:
        indicator_keys = await _indicator_keys_for_place(engine, input.place_id)

    if not indicator_keys:
        return DetectInsightsOutput(signals=[])

    signals: list[InsightSignal] = []
    for key in indicator_keys:
        signals.extend(await _detect_percentile_and_divergence(engine, input.place_id, key))
        signals.extend(await _detect_trend_reversal(engine, input.place_id, key))

    return DetectInsightsOutput(signals=signals)


async def _indicator_keys_for_place(engine: AsyncEngine, place_id: str) -> list[str]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT DISTINCT indicator_key FROM data.indicator_value WHERE place_id = :pid"
                ),
                {"pid": place_id},
            )
        ).all()
    return [r.indicator_key for r in rows]


async def _detect_percentile_and_divergence(
    engine: AsyncEngine, place_id: str, indicator_key: str
) -> list[InsightSignal]:
    """Run percentile + peer-divergence detection in one query.

    Returns 0–2 signals (one percentile if in tail, one divergence if
    beyond one stddev of the same-type-peer median).
    """
    lo, hi = value_bounds(get_contract_registry(), indicator_key)
    # Values outside the indicator's measure contract are not a place's
    # value and not a peer's: a "proportion" of 75 is a storage error.
    sql = text(
        """
        WITH place_type AS (
            SELECT type
            FROM geography.place
            WHERE id = :place_id
        ),
        queried AS (
            SELECT iv.value
            FROM data.indicator_value iv
            WHERE iv.place_id = :place_id
              AND iv.indicator_key = :key
              AND iv.value IS NOT NULL
              AND (CAST(:lo AS float8) IS NULL OR iv.value >= CAST(:lo AS float8))
              AND (CAST(:hi AS float8) IS NULL OR iv.value <= CAST(:hi AS float8))
            ORDER BY iv.retrieved_at DESC
            LIMIT 1
        ),
        peers AS (
            SELECT iv.value
            FROM data.indicator_value iv
            JOIN geography.place p ON p.id = iv.place_id
            WHERE iv.indicator_key = :key
              AND iv.value IS NOT NULL
              AND (CAST(:lo AS float8) IS NULL OR iv.value >= CAST(:lo AS float8))
              AND (CAST(:hi AS float8) IS NULL OR iv.value <= CAST(:hi AS float8))
              AND p.type = (SELECT type FROM place_type)
        ),
        stats AS (
            SELECT
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY peers.value) AS median_val,
                STDDEV_POP(peers.value) AS stddev_val,
                COUNT(*) AS peer_count
            FROM peers
        )
        SELECT
            q.value,
            pr.pct_rank,
            s.median_val,
            s.stddev_val,
            s.peer_count
        FROM queried q
        CROSS JOIN stats s
        JOIN LATERAL (
            SELECT pct_rank FROM (
                SELECT
                    peers.value,
                    PERCENT_RANK() OVER (ORDER BY peers.value) * 100 AS pct_rank
                FROM peers
            ) ranked
            WHERE ranked.value = q.value
            LIMIT 1
        ) pr ON true
        """
    )
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                sql,
                {"place_id": place_id, "key": indicator_key, "lo": lo, "hi": hi},
            )
        ).all()

    if not rows:
        return []

    r = rows[0]
    out: list[InsightSignal] = []

    pct_rank = float(r.pct_rank) if r.pct_rank is not None else None
    if pct_rank is not None:
        if pct_rank <= 5.0 or pct_rank >= 95.0:
            out.append(
                InsightSignal(
                    indicator_key=indicator_key,
                    kind="extreme_percentile",
                    severity="extreme",
                    evidence_payload={"pct_rank": pct_rank},
                )
            )
        elif pct_rank <= 10.0 or pct_rank >= 90.0:
            out.append(
                InsightSignal(
                    indicator_key=indicator_key,
                    kind="extreme_percentile",
                    severity="notable",
                    evidence_payload={"pct_rank": pct_rank},
                )
            )

    if (
        r.stddev_val is not None
        and r.median_val is not None
        and r.value is not None
        and float(r.stddev_val) > 0
    ):
        value = float(r.value)
        median = float(r.median_val)
        stddev = float(r.stddev_val)
        if abs(value - median) > stddev:
            out.append(
                InsightSignal(
                    indicator_key=indicator_key,
                    kind="peer_divergence",
                    severity="notable",
                    evidence_payload={
                        "value": value,
                        "median": median,
                        "stddev": stddev,
                        "z_distance": abs(value - median) / stddev,
                    },
                )
            )

    return out


async def _detect_trend_reversal(
    engine: AsyncEngine, place_id: str, indicator_key: str
) -> list[InsightSignal]:
    """Detect a trend reversal: latest slope sign differs from the
    average slope of the prior 3 points.

    Requires >=5 trend points so both the latest segment (2 points) and
    the prior-3 segment are well-defined.
    """
    sql = text(
        """
        WITH ordered AS (
            SELECT
                value,
                period,
                LAG(value) OVER (ORDER BY period) AS prev_value
            FROM data.trend_point
            WHERE place_id = :place_id
              AND indicator_key = :key
              AND value IS NOT NULL
        ),
        slopes AS (
            SELECT
                period,
                value - prev_value AS slope
            FROM ordered
            WHERE prev_value IS NOT NULL
        ),
        ranked AS (
            SELECT
                slope,
                ROW_NUMBER() OVER (ORDER BY period) AS rn,
                COUNT(*) OVER () AS n
            FROM slopes
        ),
        bounds AS (
            SELECT MAX(rn) AS max_rn FROM ranked
        )
        SELECT
            latest.slope AS latest_slope,
            prior.avg_slope AS prior_3_avg_slope,
            b.max_rn + 1 AS total_points
        FROM bounds b
        JOIN ranked latest ON latest.rn = b.max_rn
        JOIN LATERAL (
            SELECT AVG(slope) AS avg_slope
            FROM ranked
            WHERE rn BETWEEN b.max_rn - 3 AND b.max_rn - 1
        ) prior ON true
        """
    )
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                sql,
                {"place_id": place_id, "key": indicator_key},
            )
        ).all()

    if not rows:
        return []

    r = rows[0]
    if r.total_points is None or r.total_points < 5:
        return []
    if r.latest_slope is None or r.prior_3_avg_slope is None:
        return []

    latest = float(r.latest_slope)
    prior_avg = float(r.prior_3_avg_slope)
    if latest == 0 or prior_avg == 0:
        return []
    if (latest > 0) != (prior_avg > 0):
        return [
            InsightSignal(
                indicator_key=indicator_key,
                kind="trend_reversal",
                severity="notable",
                evidence_payload={
                    "latest_slope": latest,
                    "prior_3_avg_slope": prior_avg,
                    "total_points": int(r.total_points),
                },
            )
        ]
    return []
