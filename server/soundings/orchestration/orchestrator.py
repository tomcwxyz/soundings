"""IndicatorOrchestrator — concurrent fan-out across adapters.

Per design §4: `asyncio.gather(return_exceptions=True)`, soft 10s budget,
collects values into one list and converts adapter exceptions into caveats
without sinking the whole call. SourceRef dedup happens here so callers
don't see redundant citations.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.contracts.civil_society import (
    CauseAreaCount,
    CivilSocietyProfile,
    FunderSummary,
    GrantYearSummary,
    IncomeBucket,
    NotableOrg,
    NotableOrgs,
    RegistrationCohort,
)
from soundings.contracts.comparison import Comparison, ComparisonValue
from soundings.contracts.indicator_value import IndicatorValue
from soundings.contracts.organisation import GrantRef, OrganisationRef
from soundings.contracts.source_ref import SourceRef
from soundings.contracts.trend import Trend, TrendPoint
from soundings.orchestration.errors import (
    IndicatorNotAvailableAtLevelError,
    IndicatorNotRegisteredError,
    OrchestrationError,
)
from soundings.orchestration.registry import AdapterRegistry

DEFAULT_TIMEOUT = 10.0
# Soft budget for passthrough peer-universe fan-out per design §4 and Phase 3
# plan Task 28. Above this, the orchestrator falls back to ranking only the
# caller's highlighted places against each other with a methodology caveat.
PASSTHROUGH_PEER_BUDGET = 200
ComparisonBasis = Literal["percentile", "rank", "absolute", "rate"]
BUDGET_CAVEAT = (
    "percentile computed against caller-provided peers only; "
    "indicator is passthrough-mode at this granularity"
)


@dataclass
class OrchestrationResult:
    values: list[IndicatorValue]
    sources: list[SourceRef] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    partial: bool = False


@dataclass
class CompareResult:
    comparisons: list[Comparison]
    sources: list[SourceRef] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    partial: bool = False


@dataclass
class GetTrendResult:
    trend: Trend | None
    sources: list[SourceRef] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    partial: bool = False


@dataclass
class FindOrganisationsResult:
    organisations: list[OrganisationRef] = field(default_factory=list)
    sources: list[SourceRef] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    partial: bool = False


@dataclass
class _GrantsEnrichmentResult:
    grants_by_org: dict[str, list[GrantRef]] = field(default_factory=dict)
    sources: list[SourceRef] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    partial: bool = False


SERIES_BREAK_PREFIX = "series_break:"

# Fixed income brackets for the civil society profile. Picked to match
# the breakpoints reported in the CC sector overview (Annual Report on
# the Register). `upper=None` is the open-ended top bracket.
INCOME_BUCKETS: list[tuple[str, float, float | None]] = [
    ("<10k", 0.0, 10_000.0),
    ("10k-100k", 10_000.0, 100_000.0),
    ("100k-1m", 100_000.0, 1_000_000.0),
    ("1m-10m", 1_000_000.0, 10_000_000.0),
    ("10m+", 10_000_000.0, None),
]


class IndicatorOrchestrator:
    def __init__(self, engine: AsyncEngine, registry: AdapterRegistry) -> None:
        self._engine = engine
        self._registry = registry

    async def fetch(
        self,
        indicator_keys: list[str],
        place_id: str,
        period: str | None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> OrchestrationResult:
        # Harvest each adapter independently so a single slow source (e.g. a
        # live OSM/OpenAQ call) can't poison the whole batch. Wrapping the
        # entire gather in one wait_for used to mark EVERY indicator —
        # including fast DB-backed ones that had already resolved — as a
        # TimeoutError once any adapter blew the budget. Instead we wait up to
        # the budget, keep whatever finished, and cancel only the stragglers.
        tasks = [
            asyncio.ensure_future(self._fetch_one(key, place_id, period)) for key in indicator_keys
        ]
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in pending:
            task.cancel()
        if pending:
            # Let the cancellations settle so we don't leak warnings.
            await asyncio.gather(*pending, return_exceptions=True)

        outcomes: list[Any] = []
        for task in tasks:
            if task in done and not task.cancelled():
                exc = task.exception()
                outcomes.append(exc if exc is not None else task.result())
            else:
                outcomes.append(TimeoutError("orchestrator soft budget exceeded"))

        values: list[IndicatorValue] = []
        caveats: list[str] = []
        partial = False

        for indicator_key, outcome in zip(indicator_keys, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                partial = True
                caveats.append(self._caveat_for_failure(indicator_key, outcome))
                continue
            if outcome is None:
                partial = True
                caveats.append(f"No value for indicator {indicator_key} at {place_id}")
                continue
            values.append(outcome)

        return OrchestrationResult(
            values=values,
            sources=self._dedup_sources([v.source for v in values]),
            caveats=caveats,
            partial=partial,
        )

    async def _fetch_one(
        self, indicator_key: str, place_id: str, period: str | None
    ) -> IndicatorValue | None:
        await self._enforce_level(indicator_key, place_id)
        adapter = await self._registry.adapter_for_indicator(indicator_key)
        result: IndicatorValue | None = await adapter.fetch_indicator(
            indicator_key, place_id, period
        )
        return result

    async def _enforce_level(self, indicator_key: str, place_id: str) -> None:
        place_type, _, _ = place_id.partition(":")
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT available_at FROM catalogue.indicator WHERE key = :k"),
                    {"k": indicator_key},
                )
            ).first()
        available_at = list(row.available_at) if row else []
        if available_at and place_type not in available_at:
            raise IndicatorNotAvailableAtLevelError(indicator_key, place_id, available_at)

    @staticmethod
    def _caveat_for_failure(indicator_key: str, exc: BaseException) -> str:
        if isinstance(exc, IndicatorNotAvailableAtLevelError):
            return f"INDICATOR_NOT_AVAILABLE_AT_LEVEL: {exc}"
        if isinstance(exc, OrchestrationError):
            return f"{indicator_key}: {exc}"
        if isinstance(exc, IndicatorNotRegisteredError):
            return f"{indicator_key}: no adapter registered"
        return f"{indicator_key}: {exc.__class__.__name__}"

    @staticmethod
    def _dedup_sources(refs: list[SourceRef]) -> list[SourceRef]:
        """Dedup by (source_id, retrieved_at minute).

        Lets the UI cite a single source once per source even when many
        indicators share that source within the same orchestration call.
        """
        seen: set[tuple[str, str]] = set()
        out: list[SourceRef] = []
        for r in refs:
            minute = r.retrieved_at.replace(second=0, microsecond=0).isoformat()
            key = (r.source_id, minute)
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out

    # ----- compare_places (Phase 3 Block G) -----

    async def compare_places(
        self,
        *,
        place_ids: list[str],
        indicators: list[str],
        basis: ComparisonBasis = "percentile",
        period: str | None = None,
        context_place_ids: list[str] | None = None,
    ) -> CompareResult:
        comparisons: list[Comparison] = []
        sources: list[SourceRef] = []
        caveats: list[str] = []
        partial = False

        if not place_ids and not context_place_ids:
            return CompareResult(comparisons=[], sources=[], caveats=[], partial=False)
        peer_type, _, _ = place_ids[0].partition(":")

        for indicator_key in indicators:
            try:
                comparison, ind_caveats = await self._compare_one(
                    indicator_key=indicator_key,
                    peer_type=peer_type,
                    place_ids=place_ids,
                    basis=basis,
                    period=period,
                )
            except (
                IndicatorNotRegisteredError,
                IndicatorNotAvailableAtLevelError,
                OrchestrationError,
            ) as e:
                partial = True
                caveats.append(f"{indicator_key}: {e}")
                continue
            if comparison is None:
                partial = True
                caveats.append(f"{indicator_key}: no values returned for peer universe")
                continue

            # Build a separate Comparison for context places (e.g. parent
            # LTLA alongside LSOA peers). Context places skip level
            # enforcement, carry no percentile/rank, and are flagged
            # is_context=True so consumers can render them as reference.
            if context_place_ids:
                ctx_comparison = await self._build_context_comparison(
                    indicator_key=indicator_key,
                    context_place_ids=context_place_ids,
                    period=period,
                    peer_comparison=comparison,
                )
                if ctx_comparison is not None:
                    comparisons.append(ctx_comparison)
                    sources.append(ctx_comparison.source)

            comparisons.append(comparison)
            sources.append(comparison.source)
            caveats.extend(ind_caveats)

        return CompareResult(
            comparisons=comparisons,
            sources=self._dedup_sources(sources),
            caveats=caveats,
            partial=partial,
        )

    async def _build_context_comparison(
        self,
        *,
        indicator_key: str,
        context_place_ids: list[str],
        period: str | None,
        peer_comparison: Comparison,
    ) -> Comparison | None:
        """Fetch values for context places (skipping level enforcement).

        Context places sit outside the peer universe — their value is shown
        for reference alongside the ranked peers. Errors (indicator not
        available at that level, adapter failure, etc.) are swallowed
        silently because context is informational, not critical.
        """
        context_values: list[ComparisonValue] = []
        source_ref = peer_comparison.source
        for cid in context_place_ids:
            try:
                adapter = await self._registry.adapter_for_indicator(indicator_key)
                result: IndicatorValue | None = await adapter.fetch_indicator(
                    indicator_key, cid, period
                )
            except Exception:  # noqa: S112 — context is best-effort, swallow
                continue
            if result is None:
                continue
            context_values.append(
                ComparisonValue(
                    place_id=cid,
                    value=result.value,
                    rank=None,
                    percentile=None,
                )
            )
            source_ref = result.source
        if not context_values:
            return None
        return Comparison(
            indicator=indicator_key,
            unit=peer_comparison.unit,
            period=peer_comparison.period,
            values=context_values,
            source=source_ref,
            caveats=[],
            is_context=True,
        )

    async def _compare_one(
        self,
        *,
        indicator_key: str,
        peer_type: str,
        place_ids: list[str],
        basis: ComparisonBasis,
        period: str | None,
    ) -> tuple[Comparison | None, list[str]]:
        # Level enforcement against the first place_id is enough — the spec
        # disallows mixing types in one call.
        await self._enforce_level(indicator_key, place_ids[0])
        adapter = await self._registry.adapter_for_indicator(indicator_key)
        adapter_mode = getattr(adapter, "mode", "loader")

        ind_caveats: list[str] = []
        if adapter_mode == "loader":
            peer_values, period_used = await self._peer_values_loader(
                indicator_key=indicator_key, peer_type=peer_type, period=period
            )
        else:
            peer_values, period_used, budget_hit = await self._peer_values_passthrough(
                adapter=adapter,
                indicator_key=indicator_key,
                peer_type=peer_type,
                period=period,
                place_ids=place_ids,
            )
            if budget_hit:
                ind_caveats.append(BUDGET_CAVEAT)

        if not peer_values:
            return None, ind_caveats

        # Rate basis: divide each peer's value by population.total × 1000.
        if basis == "rate":
            populations = await self._peer_populations(peer_type=peer_type)
            peer_values = {
                pid: (val / populations[pid] * 1000.0)
                if (val is not None and populations.get(pid))
                else None
                for pid, val in peer_values.items()
            }

        ranked_by_id = _ranks_descending(peer_values)
        comparison_values: list[ComparisonValue] = []
        n_with_values = sum(1 for v in peer_values.values() if v is not None)
        for pid in place_ids:
            value = peer_values.get(pid)
            rank = ranked_by_id.get(pid)
            percentile = (
                _percentile_from_rank(rank, n_with_values)
                if (rank is not None and basis == "percentile")
                else None
            )
            if basis in {"absolute"}:
                rank = None
                percentile = None
            if basis == "rank":
                percentile = None
            comparison_values.append(
                ComparisonValue(
                    place_id=pid,
                    value=value,
                    rank=rank,
                    percentile=percentile,
                )
            )

        meta = await self._load_indicator_meta(indicator_key)
        source_ref = adapter.get_source_ref(
            retrieved_at=datetime.now(tz=UTC), cache_status="cached"
        )
        comparison = Comparison(
            indicator=indicator_key,
            unit=meta["unit"] if meta else "value",
            period=period_used,
            values=comparison_values,
            source=source_ref,
            caveats=ind_caveats,
        )
        return comparison, ind_caveats

    async def _peer_values_loader(
        self,
        *,
        indicator_key: str,
        peer_type: str,
        period: str | None,
    ) -> tuple[dict[str, float | None], str]:
        """Read all peer values in a single SELECT. Picks the most recent
        per (place, indicator) so partial seeds still rank — or the supplied
        `period` when explicit."""
        sql = (
            "SELECT DISTINCT ON (iv.place_id) iv.place_id, iv.value, iv.period "
            "FROM data.indicator_value iv "
            "JOIN geography.place p ON p.id = iv.place_id "
            "WHERE iv.indicator_key = :k AND p.type = :pt "
            "AND (CAST(:period AS text) IS NULL OR iv.period = :period) "
            "ORDER BY iv.place_id, iv.period DESC"
        )
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(sql),
                    {"k": indicator_key, "pt": peer_type, "period": period},
                )
            ).all()
        peer_values: dict[str, float | None] = {
            r.place_id: (float(r.value) if r.value is not None else None) for r in rows
        }
        # Choose a period to report: the supplied one, else the most common
        # observed period (good enough for v1; rows on partial seeds align).
        period_used = period or (rows[0].period if rows else "")
        return peer_values, str(period_used)

    async def _peer_values_passthrough(
        self,
        *,
        adapter: Any,
        indicator_key: str,
        peer_type: str,
        period: str | None,
        place_ids: list[str],
    ) -> tuple[dict[str, float | None], str, bool]:
        """For passthrough adapters, fan out across the peer universe (with
        a soft budget). Above the budget we fall back to ranking only the
        caller-provided slice — the budget caveat propagates back."""
        async with self._engine.connect() as conn:
            count_row = (
                await conn.execute(
                    text("SELECT COUNT(*) AS n FROM geography.place WHERE type = :pt"),
                    {"pt": peer_type},
                )
            ).first()
        total_peers = int(count_row.n) if count_row else 0

        if total_peers > PASSTHROUGH_PEER_BUDGET:
            fetch_targets = list(dict.fromkeys(place_ids))
            budget_hit = True
        else:
            async with self._engine.connect() as conn:
                rows = (
                    await conn.execute(
                        text("SELECT id FROM geography.place WHERE type = :pt"),
                        {"pt": peer_type},
                    )
                ).all()
            fetch_targets = [r.id for r in rows]
            budget_hit = False

        results = await asyncio.gather(
            *(adapter.fetch_indicator(indicator_key, pid, period) for pid in fetch_targets),
            return_exceptions=True,
        )

        peer_values: dict[str, float | None] = {}
        period_used = period or ""
        for pid, outcome in zip(fetch_targets, results, strict=True):
            if isinstance(outcome, BaseException) or outcome is None:
                peer_values[pid] = None
                continue
            peer_values[pid] = outcome.value
            if not period_used and outcome.period:
                period_used = outcome.period
        return peer_values, period_used, budget_hit

    async def _peer_populations(self, *, peer_type: str) -> dict[str, float]:
        """Look up the latest population.total per peer for `rate` basis."""
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT DISTINCT ON (iv.place_id) iv.place_id, iv.value "
                        "FROM data.indicator_value iv "
                        "JOIN geography.place p ON p.id = iv.place_id "
                        "WHERE iv.indicator_key = 'population.total' AND p.type = :pt "
                        "ORDER BY iv.place_id, iv.period DESC"
                    ),
                    {"pt": peer_type},
                )
            ).all()
        return {r.place_id: float(r.value) for r in rows if r.value is not None}

    async def _load_indicator_meta(self, indicator_key: str) -> dict[str, str] | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT unit FROM catalogue.indicator WHERE key = :k"),
                    {"k": indicator_key},
                )
            ).first()
        if row is None:
            return None
        return {"unit": row.unit}

    # ----- get_trend (Phase 3 Block H) -----

    async def get_trend(
        self,
        *,
        indicator_key: str,
        place_id: str,
        period_from: str | None = None,
        period_to: str | None = None,
    ) -> GetTrendResult:
        try:
            await self._enforce_level(indicator_key, place_id)
        except IndicatorNotAvailableAtLevelError as e:
            return GetTrendResult(
                trend=None,
                sources=[],
                caveats=[f"INDICATOR_NOT_AVAILABLE_AT_LEVEL: {e}"],
                partial=True,
            )
        try:
            adapter = await self._registry.adapter_for_indicator(indicator_key)
        except IndicatorNotRegisteredError as e:
            return GetTrendResult(
                trend=None, sources=[], caveats=[f"{indicator_key}: {e}"], partial=True
            )

        mode = getattr(adapter, "mode", "loader")
        if mode == "loader":
            trend = await self._loader_trend(
                adapter=adapter,
                indicator_key=indicator_key,
                place_id=place_id,
                period_from=period_from,
                period_to=period_to,
            )
        else:
            trend = await adapter.fetch_trend(indicator_key, place_id, period_from, period_to)

        if trend is None:
            return GetTrendResult(
                trend=None,
                sources=[],
                caveats=[f"No trend for indicator {indicator_key} at {place_id}"],
                partial=True,
            )

        catalogue_caveats = await self._load_indicator_caveats(indicator_key)
        general, breaks = _split_series_breaks(catalogue_caveats)
        trend.breaks_in_series = breaks
        return GetTrendResult(
            trend=trend,
            sources=[trend.source],
            caveats=[f"{indicator_key}: {c}" for c in general] if general else [],
            partial=False,
        )

    async def _loader_trend(
        self,
        *,
        adapter: Any,
        indicator_key: str,
        place_id: str,
        period_from: str | None,
        period_to: str | None,
    ) -> Trend | None:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT period, value, revised FROM data.trend_point "
                        "WHERE place_id = :pid AND indicator_key = :ik "
                        "AND (CAST(:pf AS text) IS NULL OR period >= :pf) "
                        "AND (CAST(:pt AS text) IS NULL OR period <= :pt) "
                        "ORDER BY period"
                    ),
                    {
                        "pid": place_id,
                        "ik": indicator_key,
                        "pf": period_from,
                        "pt": period_to,
                    },
                )
            ).all()
        if not rows:
            return None
        points = [
            TrendPoint(
                period=r.period,
                value=float(r.value) if r.value is not None else None,
                revised=bool(r.revised),
            )
            for r in rows
        ]
        meta = await self._load_indicator_meta(indicator_key)
        source_ref = adapter.get_source_ref(
            retrieved_at=datetime.now(tz=UTC), cache_status="cached"
        )
        return Trend(
            place_id=place_id,
            indicator=indicator_key,
            unit=meta["unit"] if meta else "value",
            points=points,
            source=source_ref,
        )

    async def _load_indicator_caveats(self, indicator_key: str) -> list[str]:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT caveats FROM catalogue.indicator WHERE key = :k"),
                    {"k": indicator_key},
                )
            ).first()
        if row is None:
            return []
        raw = row.caveats or []
        return [str(c) for c in raw]

    # ----- find_organisations_in_place (Phase 4 Block D) -----

    async def find_organisations_in_place(
        self,
        *,
        place_id: str,
        activity_filter: list[str] | None = None,
        funded_only: bool = False,
        limit: int = 50,
        enrich_grants: bool = True,
    ) -> FindOrganisationsResult:
        """Find organisations in a place via mixed-mode dispatch.

        - England/Wales: SELECT from data.organisation (CC loader)
        - Scotland/NI: FTC passthrough adapter
        - Optional 360G grant enrichment
        """
        # Resolve place country from place_id prefix
        country = self._country_from_place_id(place_id)

        all_sources: list[SourceRef] = []
        all_caveats: list[str] = []
        all_orgs: list[OrganisationRef] = []
        partial = False

        # Route based on country
        if country in ("Scotland", "Northern Ireland"):
            # Scotland/NI: use FTC passthrough
            result = await self._find_via_ftc(place_id, limit)
            all_orgs = result.organisations
            all_sources.extend(result.sources)
            all_caveats.extend(result.caveats)
            partial = result.partial
        else:
            # England/Wales: use data.organisation (CC loader)
            result = await self._find_via_cc_loader(place_id, activity_filter, limit)
            all_orgs = result.organisations
            all_sources.extend(result.sources)
            all_caveats.extend(result.caveats)
            partial = partial or result.partial

        # funded_only: would require JOIN to data.grant_record (empty in Phase 4)
        if funded_only:
            all_caveats.append("funded_only=true ignored in v1: data.grant_record not populated")

        # Optional 360G grant enrichment
        if enrich_grants and all_orgs:
            grants_result = await self._enrich_with_grants(place_id, all_orgs)
            # Merge grants into orgs
            for org in all_orgs:
                org.recent_grants = grants_result.grants_by_org.get(org.id, [])
            all_sources.extend(grants_result.sources)
            all_caveats.extend(grants_result.caveats)
            partial = partial or grants_result.partial

        # Dedupe sources
        seen = set[str]()
        deduped = []
        for s in all_sources:
            if s.source_id not in seen:
                seen.add(s.source_id)
                deduped.append(s)

        return FindOrganisationsResult(
            organisations=all_orgs[:limit],
            sources=deduped,
            caveats=all_caveats,
            partial=partial,
        )

    def _country_from_place_id(self, place_id: str) -> str | None:
        """Derive country from place_id prefix."""
        if place_id.startswith("country:S"):
            return "Scotland"
        if place_id.startswith("country:NI"):
            return "Northern Ireland"
        if place_id.startswith(("ltla24:S", "utla24:S")):
            return "Scotland"
        if place_id.startswith(("ltla24:N", "utla24:N")):
            return "Northern Ireland"
        return "England"

    async def _find_via_cc_loader(
        self, place_id: str, activity_filter: list[str] | None, limit: int
    ) -> FindOrganisationsResult:
        """SELECT from data.organisation for England/Wales.

        Sorted by latest_income DESC so the largest charities appear first.
        """
        from datetime import UTC as TZ_UTC

        from soundings.contracts.source_ref import SourceRef

        # Optional cause filter — same keyword semantics as the civil society
        # profile (ILIKE over name + objects). Wrapped in parentheses so it ANDs
        # with the whole place predicate, not just the trailing OR branch.
        kw_sql, kw_params = self._keyword_filter_sql(activity_filter)
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT o.id, o.name, o.classification,
                               o.registered_address_place_id, o.source_id, o.retrieved_at,
                               (o.raw->>'latest_income')::numeric AS latest_income,
                               o.raw->>'date_of_registration' AS date_of_registration,
                               o.raw->>'postcode' AS postcode,
                               array_agg(DISTINCT oi.place_id) FILTER (
                                   WHERE oi.place_id IS NOT NULL
                               ) AS operates_in,
                               (
                                   SELECT string_agg(DISTINCT cl.classification_label, '|')
                                   FROM data.organisation_classification cl
                                   WHERE cl.organisation_id = o.id
                                     AND cl.classification_type = 'What'
                               ) AS cause_labels
                        FROM data.organisation o
                        LEFT JOIN data.organisation_operates_in oi
                            ON oi.organisation_id = o.id
                        WHERE (o.registered_address_place_id = :pid
                            OR oi.place_id = :pid)
                        """  # noqa: S608
                        f"{kw_sql}"
                        " GROUP BY o.id, o.name, o.classification,"
                        "          o.registered_address_place_id, o.source_id, o.retrieved_at,"
                        "          (o.raw->>'latest_income')::numeric,"
                        "          o.raw->>'date_of_registration', o.raw->>'postcode'"
                        " ORDER BY MAX((o.raw->>'latest_income')::numeric) DESC NULLS LAST"
                        " LIMIT :limit"
                    ),
                    {"pid": place_id, "limit": limit, **kw_params},
                )
            ).all()

        now = datetime.now(TZ_UTC)
        orgs = []
        source_ids = set[str]()

        for row in rows:
            source_id = row.source_id or "charity_commission"
            source_ids.add(source_id)
            # Construct a Charity Commission register deep-link from the
            # registration number (the part after the colon in the id).
            register_url: str | None = None
            if row.id.startswith("charity_commission:"):
                reg_no = row.id.split(":", 1)[1]
                register_url = (
                    "https://register-of-charities.charitycommission.gov.uk/"
                    f"charity-search-/charity-details/{reg_no}"
                )
            income = float(row.latest_income) if row.latest_income is not None else None
            # Prefer structured cause labels (clean, from CC classification file)
            # over the free-text classification column (noisy charity_activities).
            if row.cause_labels:
                cause_tags = row.cause_labels.split("|")
            else:
                cause_tags = list(row.classification or [])
            orgs.append(
                OrganisationRef(
                    id=row.id,
                    name=row.name,
                    classification=cause_tags,
                    registered_address_place_id=row.registered_address_place_id,
                    operates_in_place_ids=list(row.operates_in or []),
                    recent_grants=[],
                    latest_income=income,
                    register_url=register_url,
                    date_of_registration=row.date_of_registration,
                    postcode=row.postcode,
                    source=SourceRef(
                        source_id=source_id,
                        source_label=source_id,
                        publisher="",
                        licence="",
                        retrieved_at=row.retrieved_at or now,
                        cache_status="cached",
                    ),
                )
            )

        # Batch-resolve operates_in place IDs to names for display.
        all_place_ids = set()
        for org in orgs:
            all_place_ids.update(org.operates_in_place_ids)
        if all_place_ids:
            async with self._engine.connect() as conn:
                name_rows = (
                    await conn.execute(
                        text("SELECT id, name FROM geography.place WHERE id = ANY(:ids)"),
                        {"ids": list(all_place_ids)},
                    )
                ).all()
            place_names = {r.id: r.name for r in name_rows}
            for org in orgs:
                org.operates_in_place_names = [
                    place_names[pid] for pid in org.operates_in_place_ids if pid in place_names
                ]

        # Build source refs from the source_ids we collected
        sources = [
            SourceRef(
                source_id=sid,
                source_label=sid,
                publisher="Charity Commission",
                licence="open",
                retrieved_at=now,
                cache_status="cached",
            )
            for sid in source_ids
        ]

        return FindOrganisationsResult(
            organisations=orgs,
            sources=sources,
            caveats=[],
            partial=False,
        )

    async def _find_via_ftc(self, place_id: str, limit: int) -> FindOrganisationsResult:
        """Use FTC passthrough adapter for Scotland/NI."""
        try:
            ftc_adapter = self._registry.adapter_for_source("find_that_charity")
            orgs = await ftc_adapter.fetch_organisations(place_id=place_id, limit=limit)
            sources = [orgs[0].source] if orgs else []
            return FindOrganisationsResult(
                organisations=orgs,
                sources=sources,
                caveats=[],
                partial=False,
            )
        except Exception as e:
            return FindOrganisationsResult(
                organisations=[],
                sources=[],
                caveats=[f"FTC lookup failed: {e}"],
                partial=True,
            )

    async def _enrich_with_grants(
        self, place_id: str, orgs: list[OrganisationRef]
    ) -> _GrantsEnrichmentResult:
        """Enrich CC orgs with their own recent grants from 360G.

        Per-org slice: each org gets its own grants (via
        `ThreeSixtyGivingAdapter.recent_grants_for_org`), not place-wide.
        Non-CC orgs (e.g. FTC results for Scotland/NI) are skipped.
        """
        del place_id  # per-org enrichment doesn't need it
        cc_orgs = [org for org in orgs if org.id.startswith("charity_commission:")]
        if not cc_orgs:
            return _GrantsEnrichmentResult()

        try:
            adapter = self._registry.adapter_for_source("threesixtygiving")
        except Exception as e:
            return _GrantsEnrichmentResult(
                caveats=[f"Grant enrichment failed: {e}"],
                partial=True,
            )

        grants_by_org: dict[str, list[GrantRef]] = {}
        first_source: SourceRef | None = None
        per_org_failures = 0

        for org in cc_orgs:
            try:
                org_grants = await adapter.recent_grants_for_org(org.id, limit=3)
            except Exception:
                per_org_failures += 1
                continue
            if org_grants:
                grants_by_org[org.id] = org_grants
                if first_source is None:
                    first_source = org_grants[0].source

        caveats: list[str] = []
        if per_org_failures:
            caveats.append(
                f"360G grant lookup failed for {per_org_failures} of {len(cc_orgs)} organisations"
            )
        return _GrantsEnrichmentResult(
            grants_by_org=grants_by_org,
            sources=[first_source] if first_source is not None else [],
            caveats=caveats,
            partial=bool(per_org_failures),
        )

    # ----- compute_civil_society_profile (Phase 5 / civil society slice) -----

    @staticmethod
    def _keyword_filter_sql(
        keywords: list[str] | None, *, alias: str = "o"
    ) -> tuple[str, dict[str, Any]]:
        """Build an ILIKE-any cause filter over a charity's name + objects.

        Returns an SQL fragment (prefixed with ' AND (...)') plus its bound
        params, or ('', {}) when there are no usable keywords. The fragment is
        fully parameterised — keywords come from the model, never interpolated.
        """
        if not keywords:
            return "", {}
        patterns = [f"%{k.strip()}%" for k in keywords if k and k.strip()]
        if not patterns:
            return "", {}
        sql = (
            f" AND ({alias}.name ILIKE ANY(:kw) "
            f"OR array_to_string({alias}.classification, ' ') ILIKE ANY(:kw))"
        )
        return sql, {"kw": patterns}

    async def compute_civil_society_profile(
        self,
        place_id: str,
        keywords: list[str] | None = None,
        *,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> CivilSocietyProfile:
        """Aggregate `data.organisation` rows operating in `place_id`
        into a civil society profile. Pure SQL — no upstream calls
        (except the best-effort 360G fan-out for funders + grants_by_year).

        When `keywords` is given, the profile is restricted to charities whose
        name or charitable objects match one of the terms (case-insensitive
        substring) — e.g. ["food", "poverty"] for a food-poverty question.

        When `year_from`/`year_to` are given, the registration cohort series
        is filtered to that inclusive range. Totals and income are unaffected.
        """
        kw_sql, kw_params = self._keyword_filter_sql(keywords)
        retrieved = datetime.now(tz=UTC)
        async with self._engine.connect() as conn:
            totals_row = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) AS total, "  # noqa: S608
                        "       COUNT((o.raw->>'latest_income')::numeric) AS with_income "
                        "FROM data.organisation_operates_in oi "
                        "JOIN data.organisation o ON o.id = oi.organisation_id "
                        "WHERE oi.place_id = :pid"
                        f"{kw_sql}"
                    ),
                    {"pid": place_id, **kw_params},
                )
            ).first()
            total = int(totals_row.total) if totals_row else 0
            with_income = int(totals_row.with_income) if totals_row else 0

            # Count charities with registered address in this place
            # (as opposed to those that merely operate here).
            reg_row = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) AS cnt "  # noqa: S608
                        "FROM data.organisation o "
                        "WHERE o.registered_address_place_id = :pid"
                        f"  AND o.source_id = 'charity_commission'"
                        f"{kw_sql.replace('o.', 'o.')}"
                    ),
                    {"pid": place_id, **kw_params},
                )
            ).first()
            registered_address_count = int(reg_row.cnt) if reg_row else 0

            stats_row = (
                await conn.execute(
                    text(
                        "SELECT AVG((o.raw->>'latest_income')::numeric) AS mean, "  # noqa: S608
                        "       percentile_cont(0.5) WITHIN GROUP ("
                        "         ORDER BY (o.raw->>'latest_income')::numeric"
                        "       ) AS median "
                        "FROM data.organisation_operates_in oi "
                        "JOIN data.organisation o ON o.id = oi.organisation_id "
                        "WHERE oi.place_id = :pid "
                        "  AND (o.raw->>'latest_income') IS NOT NULL"
                        f"{kw_sql}"
                    ),
                    {"pid": place_id, **kw_params},
                )
            ).first()
            mean_income = (
                float(stats_row.mean) if stats_row and stats_row.mean is not None else None
            )
            median_income = (
                float(stats_row.median) if stats_row and stats_row.median is not None else None
            )

            # One COUNT per bucket via a single query. Build bucket clauses
            # inline (safe — labels/bounds are code-controlled).
            bucket_selects = []
            for idx, (_label, lower, upper) in enumerate(INCOME_BUCKETS):
                if upper is None:
                    cond = f"(o.raw->>'latest_income')::numeric >= {lower}"
                else:
                    cond = (
                        f"(o.raw->>'latest_income')::numeric >= {lower} "
                        f"AND (o.raw->>'latest_income')::numeric < {upper}"
                    )
                bucket_selects.append(f"COUNT(*) FILTER (WHERE {cond}) AS b{idx}")
            # Values are code-controlled (INCOME_BUCKETS constants), not user input.
            bucket_sql = (
                f"SELECT {', '.join(bucket_selects)} "  # noqa: S608
                "FROM data.organisation_operates_in oi "
                "JOIN data.organisation o ON o.id = oi.organisation_id "
                "WHERE oi.place_id = :pid "
                "  AND (o.raw->>'latest_income') IS NOT NULL"
                f"{kw_sql}"
            )
            buckets_row = (
                await conn.execute(text(bucket_sql), {"pid": place_id, **kw_params})
            ).first()
            income_buckets = [
                IncomeBucket(
                    label=label,
                    lower=lower,
                    upper=upper,
                    count=int(getattr(buckets_row, f"b{idx}", 0) or 0),
                )
                for idx, (label, lower, upper) in enumerate(INCOME_BUCKETS)
            ]

            cohort_rows = (
                await conn.execute(
                    text(
                        "WITH regs AS ( "  # noqa: S608
                        "  SELECT EXTRACT(YEAR FROM "
                        "           (o.raw->>'date_of_registration')::date)::int AS y, "
                        "         COUNT(*) AS n "
                        "  FROM data.organisation_operates_in oi "
                        "  JOIN data.organisation o ON o.id = oi.organisation_id "
                        "  WHERE oi.place_id = :pid "
                        "    AND (o.raw->>'date_of_registration') IS NOT NULL "
                        f"{kw_sql}"
                        "  GROUP BY 1 "
                        "), rems AS ( "
                        "  SELECT EXTRACT(YEAR FROM (o.raw->>'date_of_removal')::date)::int AS y, "
                        "         COUNT(*) AS n "
                        "  FROM data.organisation_operates_in oi "
                        "  JOIN data.organisation o ON o.id = oi.organisation_id "
                        "  WHERE oi.place_id = :pid "
                        "    AND (o.raw->>'date_of_removal') IS NOT NULL "
                        f"{kw_sql}"
                        "  GROUP BY 1 "
                        ") "
                        "SELECT COALESCE(regs.y, rems.y) AS year, "
                        "       COALESCE(regs.n, 0) AS registered, "
                        "       COALESCE(rems.n, 0) AS removed "
                        "FROM regs FULL OUTER JOIN rems ON regs.y = rems.y "
                        "ORDER BY year"
                    ),
                    {"pid": place_id, **kw_params},
                )
            ).all()
            cohort = [
                RegistrationCohort(
                    year=int(r.year),
                    registered=int(r.registered),
                    removed=int(r.removed),
                    net=int(r.registered) - int(r.removed),
                )
                for r in cohort_rows
                if (year_from is None or int(r.year) >= year_from)
                and (year_to is None or int(r.year) <= year_to)
            ]

            # --- Task 4: notable orgs (oldest / newest / largest) + income
            # concentration. A single CTE-based query picks the three extremes
            # plus the aggregates needed for the top-3 share.
            notable_row = (
                await conn.execute(
                    text(
                        "WITH base AS ( "  # noqa: S608
                        "  SELECT o.id, o.name,"
                        "         (o.raw->>'latest_income')::numeric AS income,"
                        "         (o.raw->>'date_of_registration')::date AS reg_date"
                        "  FROM data.organisation_operates_in oi"
                        "  JOIN data.organisation o ON o.id = oi.organisation_id"
                        "  WHERE oi.place_id = :pid"
                        f"{kw_sql}"
                        "), ranked AS ("
                        "  SELECT *, ROW_NUMBER() OVER ("
                        "    ORDER BY reg_date ASC NULLS LAST) AS rn_oldest,"
                        "         ROW_NUMBER() OVER ("
                        "    ORDER BY reg_date DESC NULLS LAST) AS rn_newest,"
                        "         ROW_NUMBER() OVER ("
                        "    ORDER BY income DESC NULLS LAST) AS rn_largest"
                        "  FROM base"
                        "), agg AS ("
                        "  SELECT SUM(income) AS total_income,"
                        "         (SELECT SUM(income) FROM ("
                        "            SELECT income FROM base"
                        "            WHERE income IS NOT NULL"
                        "            ORDER BY income DESC LIMIT 3"
                        "         ) s) AS top3_income,"
                        "         COUNT(income) AS income_count"
                        "  FROM base"
                        ")"
                        " SELECT o.id, o.name, o.income, o.reg_date,"
                        "        n.id AS newest_id, n.name AS newest_name,"
                        "        n.income AS newest_income, n.reg_date AS newest_reg_date,"
                        "        l.id AS largest_id, l.name AS largest_name,"
                        "        l.income AS largest_income, l.reg_date AS largest_reg_date,"
                        "        a.total_income, a.top3_income, a.income_count"
                        " FROM ranked o"
                        " CROSS JOIN agg a"
                        " LEFT JOIN ranked n ON n.rn_newest = 1"
                        " LEFT JOIN ranked l ON l.rn_largest = 1"
                        " WHERE o.rn_oldest = 1"
                    ),
                    {"pid": place_id, **kw_params},
                )
            ).first()

            # --- Task 5: cause-area distribution (top 10).
            # Uses structured CC classification codes (What/Who/How) from
            # data.organisation_classification for clean labels. Falls back
            # to the old free-text classification column if the structured
            # table is empty (e.g. before the classification loader runs).
            cause_rows = (
                await conn.execute(
                    text(
                        "SELECT cl.classification_label AS cause, COUNT(*) AS n "  # noqa: S608
                        "FROM data.organisation_operates_in oi "
                        "JOIN data.organisation o ON o.id = oi.organisation_id "
                        "JOIN data.organisation_classification cl "
                        "  ON cl.organisation_id = oi.organisation_id "
                        "WHERE oi.place_id = :pid "
                        "  AND cl.classification_type = 'What' "
                        f"{kw_sql} "
                        "GROUP BY cl.classification_label "
                        "ORDER BY n DESC LIMIT 10"
                    ),
                    {"pid": place_id, **kw_params},
                )
            ).all()
            if not cause_rows:
                # Fallback: free-text classification column (pre-migration).
                cause_rows = (
                    await conn.execute(
                        text(
                            "SELECT unnest(o.classification) AS cause, COUNT(*) AS n "  # noqa: S608
                            "FROM data.organisation_operates_in oi "
                            "JOIN data.organisation o ON o.id = oi.organisation_id "
                            "WHERE oi.place_id = :pid AND o.classification != '{}'"
                            f"{kw_sql} "
                            "GROUP BY cause ORDER BY n DESC LIMIT 10"
                        ),
                        {"pid": place_id, **kw_params},
                    )
                ).all()
            cause_area_distribution = [
                CauseAreaCount(
                    label=str(r.cause)[:120],
                    count=int(r.n),
                )
                for r in cause_rows
                if r.cause
            ]

        normalised_keywords = [k.strip() for k in (keywords or []) if k and k.strip()]

        # --- Task 4: build the NotableOrgs object from the single notable_row.
        def _notable(
            org_id: Any,
            name: Any,
            income: Any,
            reg_date: Any,
        ) -> NotableOrg | None:
            if org_id is None:
                return None
            register_url = None
            if org_id.startswith("charity_commission:"):
                reg_no = org_id.split(":", 1)[1]
                register_url = (
                    "https://register-of-charities.charitycommission.gov.uk/"
                    f"charity-search-/charity-details/{reg_no}"
                )
            year_registered = None
            if reg_date is not None:
                try:
                    year_registered = int(str(reg_date)[:4])
                except (ValueError, TypeError):
                    year_registered = None
            return NotableOrg(
                id=org_id,
                name=name,
                register_url=register_url,
                latest_income=float(income) if income is not None else None,
                date_of_registration=str(reg_date) if reg_date is not None else None,
                year_registered=year_registered,
            )

        notable = NotableOrgs()
        if notable_row is not None:
            notable = NotableOrgs(
                oldest=_notable(
                    notable_row.id, notable_row.name, notable_row.income, notable_row.reg_date
                ),
                newest=_notable(
                    notable_row.newest_id,
                    notable_row.newest_name,
                    notable_row.newest_income,
                    notable_row.newest_reg_date,
                ),
                largest=_notable(
                    notable_row.largest_id,
                    notable_row.largest_name,
                    notable_row.largest_income,
                    notable_row.largest_reg_date,
                ),
            )
            income_count = int(notable_row.income_count or 0)
            if income_count >= 3 and notable_row.total_income:
                top3 = float(notable_row.top3_income or 0)
                total_inc = float(notable_row.total_income)
                if total_inc > 0:
                    notable = notable.model_copy(
                        update={
                            "income_concentration_top3_pct": round(top3 / total_inc * 100, 1),
                            "income_concentration_top3_total": top3,
                        }
                    )

        caveats: list[str] = []
        if normalised_keywords:
            caveats.append(
                "Filtered to charities whose name or charitable objects match: "
                + ", ".join(normalised_keywords)
                + ". Matching is keyword-based on free-text Charity Commission"
                " objects, so it may miss charities that describe the same cause"
                " differently or include loosely-related ones."
            )
        if total > 0 and with_income < total:
            missing = total - with_income
            caveats.append(
                f"{missing} of {total} charities have no income on the latest CC return."
            )
        # Explain the two counts when they differ
        if total != registered_address_count:
            caveats.append(
                f"{total} charities operate in this place (self-declared area of"
                f" operation), of which {registered_address_count} have their"
                " registered address here. The difference reflects charities"
                " registered elsewhere but operating in this area — this matches"
                " how the Charity Commission website reports charity counts."
            )

        # Top funders + grants by year: aggregate 360G grants for this place.
        # Best-effort with a 5s timeout — 360G fan-out can take 30s+ on a cold
        # cache, and funder data is supplementary, not worth blocking the
        # profile on.
        top_funders: list[FunderSummary] = []
        grants_by_year: list[GrantYearSummary] = []
        try:
            tsg_adapter = self._registry.adapter_for_source("threesixtygiving")
            grants = await asyncio.wait_for(
                tsg_adapter._fetch_grants_for_place(place_id),
                timeout=5.0,
            )
            funder_totals: dict[str, dict[str, float | int]] = {}
            for g in grants:
                funder = g.get("funder") or "Unknown"
                if not funder:
                    funder = "Unknown"
                entry = funder_totals.setdefault(funder, {"total": 0.0, "count": 0})
                entry["total"] += g.get("amount", 0.0)
                entry["count"] += 1
            top_funders = sorted(
                (
                    FunderSummary(
                        name=name,
                        grant_count=int(data["count"]),
                        total_gbp=float(data["total"]),
                    )
                    for name, data in funder_totals.items()
                ),
                key=lambda f: f.total_gbp,
                reverse=True,
            )[:10]
            if top_funders:
                caveats.append(
                    "Funder data from 360Giving covers the last 12 months only and"
                    " is limited to grants received by charities registered in this"
                    " place (England/Wales)."
                )

            # Grants by year — uses all_grants (full history), separate fan-out.
            # Reuses per-org caches already warmed above, so this is fast when
            # the 12m fan-out succeeded.
            all_grants = await asyncio.wait_for(
                tsg_adapter._fetch_all_grants_for_place(place_id),
                timeout=5.0,
            )
            year_totals: dict[int, dict[str, float | int]] = {}
            for g in all_grants:
                year = int(g["date"][:4])  # extract year from ISO date string
                entry = year_totals.setdefault(year, {"total": 0.0, "count": 0})
                entry["total"] += g.get("amount", 0.0)
                entry["count"] += 1
            grants_by_year = sorted(
                (
                    GrantYearSummary(
                        year=year,
                        grant_count=int(data["count"]),
                        total_gbp=float(data["total"]),
                    )
                    for year, data in year_totals.items()
                ),
                key=lambda y: y.year,
            )
            if grants_by_year:
                caveats.append(
                    "Grant history from 360Giving covers all available years and"
                    " is limited to grants received by charities registered in this"
                    " place (England/Wales)."
                )
        except Exception:  # noqa: S110 — best-effort, funder data is supplementary
            pass

        source = SourceRef(
            source_id="charity_commission",
            source_label="Charity Commission for England and Wales",
            publisher="Charity Commission",
            licence="OGL-UK-3.0",
            retrieved_at=retrieved,
            cache_status="cached",
        )
        sources_list = [source]
        if top_funders:
            from soundings.contracts.source_ref import SourceRef as TsgSourceRef

            sources_list.append(
                TsgSourceRef(
                    source_id="threesixtygiving",
                    source_label="360Giving",
                    publisher="360Giving",
                    licence="CC-BY-4.0",
                    retrieved_at=retrieved,
                    cache_status="cached",
                )
            )
        return CivilSocietyProfile(
            place_id=place_id,
            total_organisations=total,
            registered_address_count=registered_address_count,
            with_reported_income=with_income,
            median_income=median_income,
            mean_income=mean_income,
            income_buckets=income_buckets,
            registration_cohort=cohort,
            top_funders=top_funders,
            grants_by_year=grants_by_year,
            filter_keywords=normalised_keywords,
            sources=sources_list,
            caveats=caveats,
            partial=False,
            notable=notable,
            cause_area_distribution=cause_area_distribution,
        )


def _split_series_breaks(caveats: list[str]) -> tuple[list[str], list[str]]:
    """Partition catalogue caveats into (general, series-breaks).

    Series-break caveats use the `series_break:` prefix (Phase 3 plan Task 2
    convention). The prefix is stripped from the breaks list since it's
    structural — consumers want the human-readable note.
    """
    general: list[str] = []
    breaks: list[str] = []
    for raw in caveats:
        if raw.startswith(SERIES_BREAK_PREFIX):
            breaks.append(raw[len(SERIES_BREAK_PREFIX) :].strip())
        else:
            general.append(raw)
    return general, breaks


def _ranks_descending(peer_values: dict[str, float | None]) -> dict[str, int]:
    """Return rank (1-based, highest value = rank 1). Ties share the lower
    rank (dense ranking). Places with `None` value are excluded — they get
    no rank in the comparison output."""
    with_values = [(pid, val) for pid, val in peer_values.items() if val is not None]
    with_values.sort(key=lambda pv: pv[1], reverse=True)
    ranks: dict[str, int] = {}
    last_value: float | None = None
    last_rank = 0
    for index, (pid, val) in enumerate(with_values, start=1):
        if last_value is not None and val == last_value:
            ranks[pid] = last_rank
        else:
            ranks[pid] = index
            last_rank = index
            last_value = val
    return ranks


def _percentile_from_rank(rank: int | None, n: int) -> float | None:
    """`(below_count / (n-1)) * 100`. Median of 11 = 50.0; top = 100.0;
    bottom = 0.0. With one peer in the universe, the percentile is
    undefined — return None rather than NaN."""
    if rank is None or n <= 1:
        return None
    below = n - rank
    return below / (n - 1) * 100.0
