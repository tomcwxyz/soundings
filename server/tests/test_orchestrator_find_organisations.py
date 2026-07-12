"""Regression tests for `IndicatorOrchestrator.find_organisations_in_place`.

Pure unit tests — fake engine + fake registry, no DB. Locks in the four
bugs fixed in `fix(orchestrator): find_organisations_in_place bug fixes`:

1. `_find_via_cc_loader` builds `SourceRef(cache_status="cached")`, not
   the invalid literal `"loader"`.
2. `_find_via_ftc` and `_enrich_with_grants` call
   `registry.adapter_for_source(...)` (sync, no `await`), not the
   non-existent `get_adapter(...)`.
3. `_find_via_ftc` reads `orgs[0].source`, not the undefined `org`.
4. `_enrich_with_grants` calls `recent_grants_for_org(org.id)` per
   organisation, not the same place-wide call N times.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from soundings.contracts.organisation import GrantRef, OrganisationRef
from soundings.contracts.source_ref import SourceRef
from soundings.orchestration.orchestrator import IndicatorOrchestrator

# --- Fixtures ---------------------------------------------------------------


def _source_ref(source_id: str = "threesixtygiving") -> SourceRef:
    return SourceRef(
        source_id=source_id,
        source_label=source_id,
        publisher="Test",
        retrieved_at=datetime(2026, 5, 18, tzinfo=UTC),
        cache_status="cached",
        licence="CC-BY-4.0",
    )


def _grant(funder: str = "Test Funder", amount: float = 100.0) -> GrantRef:
    return GrantRef(
        funder=funder,
        amount=amount,
        currency="GBP",
        date="2025-06-01",
        source=_source_ref(),
    )


class _FakeRow:
    """SQLAlchemy-result-like row for the CC loader SELECT."""

    def __init__(
        self,
        id: str,
        name: str,
        classification: list[str] | None = None,
        registered_address_place_id: str | None = None,
        source_id: str = "charity_commission",
        retrieved_at: datetime | None = None,
        latest_income: float | None = None,
        date_of_registration: str | None = None,
        postcode: str | None = None,
        operates_in: list[str] | None = None,
        cause_labels: str | None = None,
    ) -> None:
        self.id = id
        self.name = name
        self.classification = classification or []
        self.registered_address_place_id = registered_address_place_id
        self.source_id = source_id
        self.retrieved_at = retrieved_at or datetime(2026, 5, 18, tzinfo=UTC)
        self.latest_income = latest_income
        self.date_of_registration = date_of_registration
        self.postcode = postcode
        self.operates_in = operates_in or []
        self.cause_labels = cause_labels


class _FakeResult:
    """Mimics SQLAlchemy Result — `.all()` returns the rows list."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


def _engine_returning(rows: list[_FakeRow]) -> Any:
    """Fake AsyncEngine whose `connect()` yields a connection that returns
    the given rows from `execute()`."""
    engine = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=_FakeResult(rows))
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    engine.connect = MagicMock(return_value=cm)
    return engine


# --- Bug 1: cache_status literal --------------------------------------------


@pytest.mark.asyncio
async def test_cc_loader_path_uses_valid_cache_status_literal() -> None:
    """Regression for `cache_status="loader"` — Pydantic only accepts
    `live|cached|stale`. Constructing a SourceRef with anything else raises
    ValidationError, which used to bubble out of the tool as INTERNAL."""
    rows = [_FakeRow(id="charity_commission:1", name="Test Charity")]
    engine = _engine_returning(rows)
    registry = MagicMock()  # CC path doesn't call the registry
    orch = IndicatorOrchestrator(engine=engine, registry=registry)

    result = await orch.find_organisations_in_place(
        place_id="ltla24:E06000004",
        enrich_grants=False,  # isolate the CC path
    )

    assert len(result.organisations) == 1
    assert result.organisations[0].source.cache_status == "cached"
    assert all(s.cache_status == "cached" for s in result.sources)


# --- Bug 2 + 3: FTC path uses adapter_for_source and orgs[0].source ---------


@pytest.mark.asyncio
async def test_ftc_path_calls_adapter_for_source_synchronously() -> None:
    """Regression for `await registry.get_adapter(...)`: the registry's
    real API is `adapter_for_source(source_id)` — sync, returns the
    adapter directly. The old call raised AttributeError, caught by
    the path's try/except and surfaced as a 'FTC lookup failed' caveat."""
    ftc_org = OrganisationRef(
        id="ftc:SC005336",
        name="A Scottish Charity",
        source=_source_ref(source_id="find_that_charity"),
    )
    ftc_adapter = MagicMock()
    ftc_adapter.fetch_organisations = AsyncMock(return_value=[ftc_org])

    registry = MagicMock()
    registry.adapter_for_source = MagicMock(return_value=ftc_adapter)

    orch = IndicatorOrchestrator(engine=MagicMock(), registry=registry)
    result = await orch.find_organisations_in_place(
        place_id="ltla24:S12000033",  # Aberdeen — routes to FTC
        enrich_grants=False,
    )

    registry.adapter_for_source.assert_called_once_with("find_that_charity")
    assert result.caveats == []
    assert len(result.organisations) == 1
    # Bug 3: source was extracted from orgs[0], not the undefined `org`
    assert len(result.sources) == 1
    assert result.sources[0].source_id == "find_that_charity"


# --- Bug 4: per-org grants, not place-wide ----------------------------------


@pytest.mark.asyncio
async def test_grants_enrichment_calls_recent_grants_for_org_per_org() -> None:
    """Regression for the broken loop that called
    `adapter.recent_grants(place_id)` once per org (same args every time).
    Now should call `recent_grants_for_org(org.id)` once per CC org."""
    rows = [
        _FakeRow(id="charity_commission:111", name="Org A"),
        _FakeRow(id="charity_commission:222", name="Org B"),
        _FakeRow(id="charity_commission:333", name="Org C"),
    ]
    engine = _engine_returning(rows)

    # Each org gets its own grant payload — proves per-org dispatch.
    grants_by_id = {
        "charity_commission:111": [_grant(funder="Funder-A")],
        "charity_commission:222": [_grant(funder="Funder-B")],
        "charity_commission:333": [],  # this org has no grants
    }

    tsg_adapter = MagicMock()

    async def _recent_grants_for_org(org_id: str, *, limit: int = 3) -> list[GrantRef]:
        return grants_by_id[org_id]

    tsg_adapter.recent_grants_for_org = AsyncMock(side_effect=_recent_grants_for_org)

    registry = MagicMock()
    registry.adapter_for_source = MagicMock(return_value=tsg_adapter)

    orch = IndicatorOrchestrator(engine=engine, registry=registry)
    result = await orch.find_organisations_in_place(
        place_id="ltla24:E06000004",
        enrich_grants=True,
    )

    # Exactly one call per org, each with the org's own id.
    assert tsg_adapter.recent_grants_for_org.call_count == 3
    called_ids = [c.args[0] for c in tsg_adapter.recent_grants_for_org.call_args_list]
    assert sorted(called_ids) == [
        "charity_commission:111",
        "charity_commission:222",
        "charity_commission:333",
    ]
    # And each org carries its own grants — not Org A's grants attached to Org C.
    by_id = {o.id: o for o in result.organisations}
    assert by_id["charity_commission:111"].recent_grants[0].funder == "Funder-A"
    assert by_id["charity_commission:222"].recent_grants[0].funder == "Funder-B"
    assert by_id["charity_commission:333"].recent_grants == []


@pytest.mark.asyncio
async def test_grants_enrichment_skips_non_cc_orgs() -> None:
    """FTC orgs (id starts with `ftc:`) shouldn't trigger 360G lookups."""
    ftc_org = OrganisationRef(
        id="ftc:SC005336",
        name="A Scottish Charity",
        source=_source_ref(source_id="find_that_charity"),
    )
    ftc_adapter = MagicMock()
    ftc_adapter.fetch_organisations = AsyncMock(return_value=[ftc_org])
    tsg_adapter = MagicMock()
    tsg_adapter.recent_grants_for_org = AsyncMock(return_value=[])

    registry = MagicMock()
    registry.adapter_for_source = MagicMock(
        side_effect=lambda sid: ftc_adapter if sid == "find_that_charity" else tsg_adapter
    )

    orch = IndicatorOrchestrator(engine=MagicMock(), registry=registry)
    await orch.find_organisations_in_place(
        place_id="ltla24:S12000033",
        enrich_grants=True,
    )

    tsg_adapter.recent_grants_for_org.assert_not_called()


@pytest.mark.asyncio
async def test_per_org_grant_failure_surfaces_as_caveat_not_crash() -> None:
    """If `recent_grants_for_org` raises for one org, the others should
    still get their grants, and the failure surfaces as a caveat."""
    rows = [
        _FakeRow(id="charity_commission:111", name="Org A"),
        _FakeRow(id="charity_commission:222", name="Org B"),
    ]
    engine = _engine_returning(rows)

    tsg_adapter = MagicMock()

    async def _recent_grants_for_org(org_id: str, *, limit: int = 3) -> list[GrantRef]:
        if org_id == "charity_commission:111":
            raise RuntimeError("upstream 503")
        return [_grant(funder="Funder-B")]

    tsg_adapter.recent_grants_for_org = AsyncMock(side_effect=_recent_grants_for_org)
    registry = MagicMock()
    registry.adapter_for_source = MagicMock(return_value=tsg_adapter)

    orch = IndicatorOrchestrator(engine=engine, registry=registry)
    result = await orch.find_organisations_in_place(
        place_id="ltla24:E06000004",
        enrich_grants=True,
    )

    assert any("360G grant lookup failed for 1" in c for c in result.caveats)
    assert result.partial is True
    by_id = {o.id: o for o in result.organisations}
    assert by_id["charity_commission:111"].recent_grants == []
    assert by_id["charity_commission:222"].recent_grants[0].funder == "Funder-B"
