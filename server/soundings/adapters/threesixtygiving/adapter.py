"""ThreeSixtyGivingAdapter — local-first 360Giving place intelligence.

Once Soundings has completed a full GrantNav import, place-level grant
indicators and recent-grant lookups are served from ``data.grant_record``.
Before that first successful full import, the existing organisation-centric
360Giving API fan-out remains available as a compatibility fallback.

Indicators (catalogue keys):
- ``civil_society.grants_in_last_12m_total`` — sum of GBP grants awarded into
  the place in the last 12 months.
- ``civil_society.grants_in_last_12m_count`` — count of those grants.

The full-corpus path prefers explicit GrantNav beneficiary geography. Recipient
organisation geography is used only where a grant has no beneficiary geography.
This better matches the catalogue's "awarded into the area" language and avoids
API fan-out once the local evidence layer is complete.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.passthrough_base import PassthroughAdapter
from soundings.adapters.threesixtygiving.client import (
    OrgAggregate,
    ThreeSixtyGivingClient,
)
from soundings.contracts.indicator_value import IndicatorValue
from soundings.contracts.organisation import GrantRef
from soundings.grants.status import has_complete_grant_index
from soundings.grants.store import GrantStore

SOURCE_ID = "threesixtygiving"
DEFAULT_TTL = timedelta(days=7)
LAST_12M_DAYS = 365
INDICATOR_TOTAL = "civil_society.grants_in_last_12m_total"
INDICATOR_COUNT = "civil_society.grants_in_last_12m_count"
LOCAL_INDEX_CAVEAT = (
    "Based on grants available in the current GrantNav full-corpus index; "
    "360Giving coverage varies by funder and unpublished grants are not represented."
)


class ThreeSixtyGivingAdapter(PassthroughAdapter):
    source_id = SOURCE_ID

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        ttl: timedelta = DEFAULT_TTL,
        threesixtygiving_client: ThreeSixtyGivingClient | None = None,
        http_client: httpx.AsyncClient | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        super().__init__(engine, ttl=ttl, http_client=http_client)
        self._tsg = threesixtygiving_client or ThreeSixtyGivingClient(http_client=http_client)
        self._now = now
        self._grant_store = GrantStore(engine)

    # ----- IndicatorValue path --------------------------------------------

    async def fetch_indicator(
        self,
        indicator_key: str,
        place_id: str,
        period: str | None,
    ) -> IndicatorValue | None:
        if indicator_key not in (INDICATOR_TOTAL, INDICATOR_COUNT):
            return None

        using_local_index = await has_complete_grant_index(self._engine)
        caveats: list[str] = []

        if using_local_index:
            cutoff = (self._now() - timedelta(days=LAST_12M_DAYS)).date()
            aggregate = await self._grant_store.aggregate_for_place(
                place_id,
                awarded_from=cutoff,
            )
            if indicator_key == INDICATOR_TOTAL:
                value = float(aggregate["total_gbp"])
                unit = "GBP"
            else:
                value = float(aggregate["grants"])
                unit = "grants"
            caveats.append(LOCAL_INDEX_CAVEAT)
        else:
            grants = await self._fetch_grants_for_place(place_id)
            if indicator_key == INDICATOR_TOTAL:
                value = sum(g["amount"] for g in grants)
                unit = "GBP"
            else:
                value = float(len(grants))
                unit = "grants"

            if not await self._has_charities_for_place(place_id):
                caveats.append(
                    "no charities registered for this place in data.organisation — "
                    "CC loader hasn't run for this LTLA yet, or the place lies "
                    "outside England and Wales"
                )

        source_ref = await self._build_source_ref(retrieved_at=self._now(), cache_status="cached")
        return IndicatorValue(
            place_id=place_id,
            indicator=indicator_key,
            value=value,
            unit=unit,
            period=self._now().strftime("%Y-%m"),
            source=source_ref,
            caveats=caveats,
            confidence="official",
        )

    # ----- Block D helpers ------------------------------------------------

    async def recent_grants(self, place_id: str, *, limit: int = 3) -> list[GrantRef]:
        if await has_complete_grant_index(self._engine):
            cutoff = (self._now() - timedelta(days=LAST_12M_DAYS)).date()
            rows = await self._grant_store.list_for_place(
                place_id,
                awarded_from=cutoff,
                limit=limit,
            )
            grants = [g for row in rows if (g := _indexed_grant_to_legacy(row)) is not None]
        else:
            grants = await self._fetch_grants_for_place(place_id)
            grants = sorted(grants, key=lambda g: g["date"], reverse=True)[:limit]

        source_ref = await self._build_source_ref(retrieved_at=self._now(), cache_status="cached")
        return [
            GrantRef(
                funder=g["funder"],
                amount=g["amount"],
                currency="GBP",
                date=g["date"],
                purpose=g.get("purpose"),
                source=source_ref,
            )
            for g in grants[:limit]
        ]

    async def recent_grants_for_org(self, org_id: str, *, limit: int = 3) -> list[GrantRef]:
        """Return an organisation's newest grants, local-first after full import."""
        if await has_complete_grant_index(self._engine):
            rows = await self._grant_store.list_for_recipient(org_id, limit=limit)
            grants = [g for row in rows if (g := _indexed_grant_to_legacy(row)) is not None]
        else:
            tsg_org_id = _cc_to_tsg_org_id(org_id)
            grants = await self._cached_org_grants(tsg_org_id)
            grants = sorted(grants, key=lambda g: g["date"], reverse=True)[:limit]

        source_ref = await self._build_source_ref(retrieved_at=self._now(), cache_status="cached")
        return [
            GrantRef(
                funder=g["funder"],
                amount=g["amount"],
                currency="GBP",
                date=g["date"],
                purpose=g.get("purpose"),
                source=source_ref,
            )
            for g in grants[:limit]
        ]

    # ----- Core grant access ----------------------------------------------

    async def _fetch_all_grants_for_place(self, place_id: str) -> list[dict[str, Any]]:
        """Return full grant history for temporal aggregation.

        A complete local GrantNav corpus is authoritative for this access path.
        Until one exists, retain the legacy per-organisation API/cache fan-out.
        """
        if await has_complete_grant_index(self._engine):
            rows = await self._grant_store.list_for_place(place_id)
            return [g for row in rows if (g := _indexed_grant_to_legacy(row)) is not None]

        place_cache_key = f"360g:place_all_grants:{place_id}"
        cached = await self._cache.get(self.source_id, place_cache_key)
        if cached is not None and isinstance(cached, list):
            for g in cached:
                g["date_obj"] = _parse_iso_date(g["date"])
            return cached

        org_ids = await self._cc_org_ids_for_place(place_id)
        if not org_ids:
            empty: list[dict[str, Any]] = []
            await self._cache.put(self.source_id, place_cache_key, empty, ttl=self._ttl)
            return empty

        all_grants: list[dict[str, Any]] = []
        for org_id in org_ids:
            tsg_org_id = _cc_to_tsg_org_id(org_id)
            aggregate = await self._cached_org_aggregate(tsg_org_id)
            if aggregate is None:
                continue
            if not aggregate.latest_grant_date and not aggregate.earliest_grant_date:
                continue
            org_grants = await self._cached_org_grants(tsg_org_id)
            all_grants.extend(org_grants)

        compact = [{k: v for k, v in g.items() if k != "date_obj"} for g in all_grants]
        await self._cache.put(self.source_id, place_cache_key, compact, ttl=self._ttl)
        for g in compact:
            g["date_obj"] = _parse_iso_date(g["date"])
        return compact

    async def _fetch_grants_for_place(self, place_id: str) -> list[dict[str, Any]]:
        """Legacy live fallback for recent grants received by local charities.

        This path is used only before a successful full GrantNav import. It is
        kept intact so new deployments remain useful while the corpus bootstrap
        is still pending.
        """
        place_cache_key = f"360g:place_grants:{place_id}"
        cached = await self._cache.get(self.source_id, place_cache_key)
        if cached is not None and isinstance(cached, list):
            return cached

        org_ids = await self._cc_org_ids_for_place(place_id)
        if not org_ids:
            empty: list[dict[str, Any]] = []
            await self._cache.put(self.source_id, place_cache_key, empty, ttl=self._ttl)
            return empty

        cutoff = self._now() - timedelta(days=LAST_12M_DAYS)
        all_grants: list[dict[str, Any]] = []

        for org_id in org_ids:
            tsg_org_id = _cc_to_tsg_org_id(org_id)
            aggregate = await self._cached_org_aggregate(tsg_org_id)
            if aggregate is None:
                continue
            if (
                aggregate.latest_grant_date
                and _parse_iso_date(aggregate.latest_grant_date) < cutoff.date()
            ):
                continue

            org_grants = await self._cached_org_grants(tsg_org_id)
            for grant in org_grants:
                if grant["date_obj"] >= cutoff.date():
                    all_grants.append(grant)

        compact = [{k: v for k, v in g.items() if k != "date_obj"} for g in all_grants]
        await self._cache.put(self.source_id, place_cache_key, compact, ttl=self._ttl)
        for g in compact:
            g["date_obj"] = _parse_iso_date(g["date"])
        return compact

    async def _cached_org_aggregate(self, tsg_org_id: str) -> OrgAggregate | None:
        cache_key = f"360g:agg:{tsg_org_id}"
        cached = await self._cache.get(self.source_id, cache_key)
        if cached is not None:
            if cached == "__none__":
                return None
            if isinstance(cached, dict):
                return OrgAggregate(**cached)
        aggregate = await self._tsg.get_org_aggregate(tsg_org_id)
        if aggregate is None:
            await self._cache.put(self.source_id, cache_key, "__none__", ttl=self._ttl)
            return None
        await self._cache.put(self.source_id, cache_key, aggregate.__dict__, ttl=self._ttl)
        return aggregate

    async def _cached_org_grants(self, tsg_org_id: str) -> list[dict[str, Any]]:
        cache_key = f"360g:grants:{tsg_org_id}"
        cached = await self._cache.get(self.source_id, cache_key)
        if cached is not None and isinstance(cached, list):
            for g in cached:
                g["date_obj"] = _parse_iso_date(g["date"])
            return cached

        grants: list[dict[str, Any]] = []
        async for raw in self._tsg.iter_grants_received(tsg_org_id):
            grant = _materialise_grant(raw)
            if grant is None:
                continue
            grants.append(grant)

        compact = [{k: v for k, v in g.items() if k != "date_obj"} for g in grants]
        await self._cache.put(self.source_id, cache_key, compact, ttl=self._ttl)
        for g in compact:
            g["date_obj"] = _parse_iso_date(g["date"])
        return compact

    # ----- Lookups --------------------------------------------------------

    async def _cc_org_ids_for_place(self, place_id: str) -> list[str]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT id FROM data.organisation "
                        "WHERE source_id = 'charity_commission' "
                        "AND registered_address_place_id = :pid "
                        "ORDER BY id"
                    ),
                    {"pid": place_id},
                )
            ).all()
        return [r.id for r in rows]

    async def _has_charities_for_place(self, place_id: str) -> bool:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM data.organisation "
                        "WHERE source_id = 'charity_commission' "
                        "AND registered_address_place_id = :pid "
                        "LIMIT 1"
                    ),
                    {"pid": place_id},
                )
            ).first()
        return row is not None

    async def _call_upstream(self, client: httpx.AsyncClient, cache_key: str) -> Any:
        del client, cache_key
        raise NotImplementedError("ThreeSixtyGivingAdapter routes via fetch_indicator override")

    # ----- pre_warmer hook ------------------------------------------------

    async def pre_warm_for_places(self, place_ids: list[str]) -> None:
        """Warm legacy API caches only until the full local corpus exists."""
        if await has_complete_grant_index(self._engine):
            return

        import logging

        log = logging.getLogger(__name__)
        for place_id in place_ids:
            try:
                await self._fetch_grants_for_place(place_id)
            except Exception:
                log.exception("360G pre_warm failed for place_id=%s", place_id)


# --- helpers ----------------------------------------------------------------


def _cc_to_tsg_org_id(cc_id: str) -> str:
    """data.organisation `charity_commission:1234` → 360G `GB-CHC-1234`."""
    if cc_id.startswith("charity_commission:"):
        return "GB-CHC-" + cc_id.split(":", 1)[1]
    return cc_id


def _indexed_grant_to_legacy(row: dict[str, Any]) -> dict[str, Any] | None:
    """Map an indexed grant onto the adapter's historic internal shape."""
    awarded_on = row.get("awarded_on")
    amount = row.get("amount")
    if not awarded_on or amount is None:
        return None
    iso_date = awarded_on.isoformat() if isinstance(awarded_on, date) else str(awarded_on)[:10]
    try:
        date_obj = _parse_iso_date(iso_date)
    except ValueError:
        return None
    return {
        "date": iso_date,
        "date_obj": date_obj,
        "amount": float(amount),
        "funder": str(row.get("funder_name") or row.get("funder_id") or ""),
        "purpose": row.get("purpose") or row.get("title"),
    }


def _materialise_grant(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten a live 360G grant payload into the legacy cache shape.

    Only GBP grants are counted; non-GBP grants are dropped because the
    catalogue indicators have a fixed GBP unit.
    """
    data = raw.get("data") or {}
    if data.get("currency") != "GBP":
        return None
    award_date = data.get("awardDate")
    amount = data.get("amountAwarded")
    if not award_date or amount is None:
        return None
    iso_date = str(award_date)[:10]
    try:
        date_obj = _parse_iso_date(iso_date)
    except ValueError:
        return None
    funders = data.get("fundingOrganization") or []
    funder_name = ""
    if funders and isinstance(funders, list):
        first = funders[0]
        if isinstance(first, dict):
            funder_name = str(first.get("name") or first.get("id") or "")
    return {
        "date": iso_date,
        "date_obj": date_obj,
        "amount": float(amount),
        "funder": funder_name,
        "purpose": data.get("description") or data.get("title"),
    }


def _parse_iso_date(iso: str) -> date:
    return date.fromisoformat(iso[:10])
