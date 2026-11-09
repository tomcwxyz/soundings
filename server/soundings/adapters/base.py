from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.source_ref_factory import SourceRefFactory
from soundings.contracts.indicator_value import Confidence, IndicatorValue
from soundings.contracts.source_ref import CacheStatus, SourceRef


@dataclass
class LoaderResult:
    rows_written: int
    notes: str | None = None


def _cron_to_window_days(cron: str | None) -> int:
    """Crude estimate of a cron schedule's period in days.

    Recognises the patterns we use in catalogue/sources.yaml; falls back to
    30 days for anything unrecognised. Used only to set cache_status, not
    to schedule anything — APScheduler parses the cron itself.
    """
    if not cron:
        return 30
    parts = cron.split()
    if len(parts) != 5:
        return 30
    _minute, _hour, dom, month, dow = parts

    # A list of release months is not a single annual release (e.g. NSPL).
    if "," in month and dom != "*":
        try:
            months = sorted({int(value) for value in month.split(",")})
            if all(1 <= value <= 12 for value in months):
                gaps = [
                    later - earlier
                    for earlier, later in zip(months, [*months[1:], months[0] + 12], strict=True)
                ]
                return max(gaps) * 30
        except ValueError:
            return 30

    # Yearly: month is a fixed value AND day-of-month is fixed.
    if month != "*" and "/" not in month and dom != "*":
        return 365
    # Quarterly / N-monthly: month is */N.
    if "/" in month:
        try:
            return int(month.split("/")[1]) * 30
        except ValueError:
            return 90
    # Monthly: day-of-month is fixed (e.g. 1st of the month).
    if dom != "*" and "/" not in dom:
        return 30
    # Weekly: day-of-week is fixed.
    if dow != "*" and "/" not in dow:
        return 7
    return 1


class LoaderAdapter(ABC):
    """Base class for loader-mode adapters.

    Loader adapters fetch a whole dataset from upstream and upsert it into
    Postgres. They run as part of `make seed` and on a refresh cadence.
    Each invocation is wrapped in a `data.loader_run` row.

    `fetch_indicator` reads from `data.indicator_value` and stamps the
    response with a `cache_status` derived from the most recent successful
    `loader_run`.
    """

    source_id: str
    mode = "loader"
    default_confidence: Confidence = "official"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._source_ref_factory = SourceRefFactory(engine)

    @abstractmethod
    async def load(self, run_id: str | None = None) -> LoaderResult:
        """Fetch from upstream and upsert into Postgres."""
        ...

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(tz=UTC)

    async def list_available_indicators(self) -> list[str]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text("SELECT key FROM catalogue.indicator WHERE source_id = :sid"),
                    {"sid": self.source_id},
                )
            ).all()
        return [r.key for r in rows]

    async def fetch_indicator(
        self,
        indicator_key: str,
        place_id: str,
        period: str | None,
    ) -> IndicatorValue | None:
        async with self._engine.connect() as conn:
            value_row = (
                await conn.execute(
                    text(
                        "SELECT iv.value, iv.period, iv.caveats, iv.retrieved_at, ind.unit "
                        "FROM data.indicator_value iv "
                        "JOIN catalogue.indicator ind ON ind.key = iv.indicator_key "
                        "WHERE iv.place_id = :pid AND iv.indicator_key = :ik "
                        "AND iv.period = COALESCE(:period, iv.period) "
                        "ORDER BY iv.period DESC LIMIT 1"
                    ),
                    {"pid": place_id, "ik": indicator_key, "period": period},
                )
            ).first()
            if value_row is None:
                return None

            run_row = (
                await conn.execute(
                    text(
                        "SELECT finished_at FROM data.loader_run "
                        "WHERE source_id = :sid AND status = 'ok' "
                        "ORDER BY finished_at DESC LIMIT 1"
                    ),
                    {"sid": self.source_id},
                )
            ).first()

            cadence_row = (
                await conn.execute(
                    text("SELECT refresh_cadence FROM catalogue.source WHERE id = :sid"),
                    {"sid": self.source_id},
                )
            ).first()
        if cadence_row is None:
            return None

        retrieved_at = value_row.retrieved_at
        cache_status = self._compute_cache_status(
            run_row.finished_at if run_row else None,
            cadence_row.refresh_cadence,
        )
        source_ref = await self._source_ref_factory.build(
            self.source_id, retrieved_at=retrieved_at, cache_status=cache_status
        )
        if source_ref is None:
            return None
        return IndicatorValue(
            place_id=place_id,
            indicator=indicator_key,
            value=float(value_row.value) if value_row.value is not None else None,
            unit=value_row.unit,
            period=value_row.period,
            source=source_ref,
            caveats=list(value_row.caveats or []),
            confidence=self.default_confidence,
        )

    def get_source_ref(
        self,
        *,
        retrieved_at: datetime,
        cache_status: CacheStatus,
    ) -> SourceRef:
        # Subclasses can override; default builds from in-memory metadata
        # if the catalogue row hasn't been loaded yet.
        return SourceRef(
            source_id=self.source_id,
            source_label=self.source_id,
            publisher="",
            licence="",
            retrieved_at=retrieved_at,
            cache_status=cache_status,
        )

    @staticmethod
    def _compute_cache_status(
        last_finished_at: datetime | None,
        refresh_cadence: str | None,
    ) -> CacheStatus:
        if last_finished_at is None:
            return "stale"
        window_days = _cron_to_window_days(refresh_cadence)
        age = datetime.now(tz=UTC) - last_finished_at
        if age <= timedelta(days=window_days):
            return "cached"
        if age <= timedelta(days=int(window_days * 1.5)):
            return "cached"
        return "stale"
