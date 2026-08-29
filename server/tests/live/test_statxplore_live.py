"""Live test for dwp.statxplore against the real Stat-Xplore API.

Marker `live` — runs nightly, not in PR CI. Requires `STATXPLORE_API_KEY`
in the env (a GitHub Actions Secret in CI). Skipped with a clear
message when the key is missing.

Stat-Xplore's `/schema` endpoint is auth-gated. The Universal Credit
mapping exercised here was verified against the live authenticated schema
on 29 August 2026; this test guards against future schema drift.
"""

import os

import pytest
from sqlalchemy import text

from soundings.adapters.dwp_statxplore.adapter import DwpStatXploreAdapter
from soundings.db.engine import get_engine

pytestmark = [pytest.mark.live, pytest.mark.integration]


@pytest.mark.skipif(
    not os.environ.get("STATXPLORE_API_KEY"),
    reason="STATXPLORE_API_KEY not set — sign up at https://stat-xplore.dwp.gov.uk/",
)
async def test_statxplore_adapter_returns_plausible_uc_caseload() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.trend_point"))
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM cache.source_cache"))
        await conn.execute(text("DELETE FROM geography.postcode"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) "
                "VALUES ('ltla24:E06000004', 'ltla24', 'E06000004', "
                "'Stockton-on-Tees')"
            )
        )

    adapter = DwpStatXploreAdapter(engine)
    iv = await adapter.fetch_indicator(
        "economy.universal_credit_claimants", "ltla24:E06000004", period=None
    )

    assert iv is not None, "Stat-Xplore returned no UC caseload for Stockton-on-Tees"
    assert iv.value is not None
    # Stockton-on-Tees population ~200k; UC caseload is a meaningful
    # fraction of working-age — sanity-check it's a non-trivial integer.
    assert iv.value > 1000, f"implausibly low UC caseload: {iv.value}"
    assert iv.value < 200_000, f"implausibly high UC caseload: {iv.value}"
    assert iv.unit == "people"
    assert iv.source.source_id == "dwp.statxplore"
