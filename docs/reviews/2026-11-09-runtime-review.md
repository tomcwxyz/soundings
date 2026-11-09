# Runtime review — 9 November 2026

## Completed

- Shared tracking now records complete scheduled, one-shot and seed imports,
  including failed/cancelled imports. Partial writes cannot create success history.
- Health distinguishes failed imports, overdue verified imports, missing history
  and configured sources with no loader. Database test fixtures and manual
  sign-ups are excluded from scheduled dataset monitoring.
- Indicator provenance preserves the stored retrieval date. A later loader run
  or absent history no longer gives old values a misleading retrieval date.
- Sport England's existing loader is registered. Quarterly postcode cadence is
  recognised. Companies House runs on the 10th, avoiding the November race where
  the files appeared after the old 7th-at-05:00 job.
- Full Charity Commission refresh: 170,757 imported charities; November counts
  cover 165,100 postcode-matched charities across 358 areas. Unresolved postcodes
  remain excluded and caveated. Historical failure cause is unknown; a late-stage
  interruption is consistent with the observed register/aggregate mismatch.
- Full November Companies House refresh: 359 areas and 1,077 indicator rows;
  16,458 unresolved postcodes remain excluded and caveated.
- A complete tracked IMD 2025 refresh was validated and applied: 238,357
  indicator/aggregate rows, clearing the legacy verified-import warning.
- Full ONS refresh: repaired MSOA and administrative lookup URLs; ArcGIS JSON
  errors under HTTP 200 are rejected. Country display boundaries use the now
  available BUC layer. Historical place inserts are batched below the database
  parameter limit. No constituency overlaps were promoted to containment.
- All current ONS layers have nonempty geometry: 35,672 LSOAs, 7,264 MSOAs,
  361 lower-tier authorities, 218 upper-tier authorities, 8,396 wards,
  650 constituencies, nine regions and four countries.
- Ask INFO measurements are enabled under the actual Uvicorn setup, confined to
  the Ask logger namespace. Cache messages no longer log question text; unrelated
  auth and HTTP client INFO output remains disabled.

## Hands-on checks from the morning review

- Public pages and real MCP SSE initialization/tool calls pass through the tunnel.
- The explorer renders the national layer and drills into North Yorkshire's
  379 neighbourhoods. Harrogate 007A can be selected and added to comparison;
  that selection survives switching from overall IMD to income deprivation.
- External MCP workflow resolves Middlesbrough, discovers neighbourhood IMD,
  returns the five highest-score neighbourhoods, and corroborates Middlesbrough
  004C's score of 79.998 for 2025 through `get_indicators` with provenance.
- Current evaluation set contains **44 questions**, not the review's older 36.
  Static validation passes and covers all 15 non-terminal Ask tools.
- No model-backed answer scoring has been performed in this follow-up while the
  paid-evaluation budget decision is pending. A read-only provider check confirms
  the configured model is available; this is not proof of answer quality.

## Remaining findings and sequencing

1. Run a budget-bounded answer evaluation and score question fit, grounding,
   geography, context, coverage honesty and presentation using the existing rubric.
   Record real model/tool latency and usage before changing concurrency or models.
2. `get_sub_areas` currently returns an empty source list: a separate indicator
   lookup supplies provenance, but neighbourhood answers should carry their own
   source references and caveats. Address this contract gap with regression tests.
3. Older sources without recorded complete imports remain explicitly untracked;
   do not backfill fabricated successes from individual row timestamps. MHCLG live
   tables remain configured but unimplemented. IMD 2019 retains data but its last
   recorded refresh failed on a retired URL.
4. Use evaluation failures to select the next coverage work (claimant count,
   affordability, child poverty, Scottish deprivation or Northern Ireland), and
   verify numerator/denominator, vintage and nation semantics before adding data.
5. Defer transport migration, MCP Apps, vector tiles and broader visual changes
   until evidence justifies them. Keep the grant index outside this follow-up.

Verification: 864 non-live server tests passed before the final country/batching
repair, followed by four historical-place integration tests, 478 unit tests,
strict type checking, lint, repository hooks, isolated full refreshes and public
runtime checks. No remote push or PR merge was performed.
