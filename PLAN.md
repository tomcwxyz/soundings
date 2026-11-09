# Plan

> Last updated: 2026-06-30
> Status: **Phase 5 complete.** Phase 6 runs as two tracks — **6a (depth)
> shipping**, **6b (breadth) in planning**.

## Objective

Build Soundings v1 — a single MCP server wrapping a curated set of UK open
data sources behind a small set of question-shaped tools, with every consented
question logged to a public corpus. v1 is sequenced into ~6 phases over
roughly 10–12 weeks per the build plan in `docs/v1-orchestration-and-capture.md` §13.

## Approach

Per-phase plans live in `docs/plans/`:

- `docs/plans/2026-05-05-soundings-v1-design.md` — implementation design.
- `docs/plans/2026-05-05-soundings-v1-phase-0-plan.md` — Phase 0 plan (40 tasks).
- `docs/plans/2026-05-10-soundings-v1-phase-1-plan.md` — Phase 1 plan (52 tasks).
- `docs/plans/2026-05-11-soundings-v1-phase-2-plan.md` — Phase 2 plan (45 tasks).
- `docs/plans/2026-05-11-soundings-v1-phase-3-plan.md` — Phase 3 plan (45 tasks).

Each phase plan is TDD task-by-task: failing test → minimum implementation →
green → conventional-commit. Block-level commit boundaries. From Phase 3 Block E
onwards each block lands as a single squash-merged PR rather than direct commits
to `main`.

## Freshness follow-up — 9 November 2026

- [x] Record scheduled imports, correct freshness categories and retrieval dates.
- [x] Repair/validate/refresh Charity Commission, Companies House and ONS geography.
- [x] Verify public map/MCP behavior and enable privacy-preserving Ask measurements.
- [ ] Score the current 44-question Ask baseline within an approved budget.
- [ ] Return neighbourhood source references/caveats directly from `get_sub_areas`.

See `docs/reviews/2026-11-09-runtime-review.md` for evidence and sequencing.

## Local integration — 9 November 2026

- [x] Combine PRs #65 and #66 on a local deployment branch and check tool defaults.
- [x] Validate Welsh deprivation and OSCR imports against an isolated database.
- [x] Complete Scottish geography import validation and runtime/public smoke checks.

## Tasks

- [x] Phase 0 — Repo scaffolding, schema, geography spine.
      Tag `v0.1.0-phase-0`.
- [x] Phase 1 — Three indicator adapters (MYE, Census, IMD); three tools
      (`find_place`, `get_indicators`, `get_place_profile`); HTTP + MCP
      transports; loader daemon. Tag `v0.2.0-phase-1`.
- [x] Phase 2 — Capture pipeline (raw + sanitised, two-step write), six
      sanitisation rules, monthly publication, minimal Astro UI (`/`,
      `/place/[id]`, `/about`), Resend alerts. Tag `v0.3.0-phase-2`.
- [x] Phase 3 — Adapters for Fingertips, DWP Stat-Xplore, DfE, police.uk,
      ONS APS; `compare_places` and `get_trend` tools; UI sparklines on
      `/place/[id]` and a new `/compare` page. Tag `v0.4.0-phase-3`
      pending browser smoke.
- [x] Phase 4 — Adapters for Charity Commission (loader; bulk register),
      360Giving + Find That Charity (passthrough);
      `find_organisations_in_place`. **Blocks 0, A, B, C, D, E, F landed**.
- [x] Phase 5 — First monthly corpus release, doc pass. **Complete.**
- [x] UI Design — Apply Good Ship branding, new design system. **Complete.**
- [x] Phase 6 — Civil society profile slice (`get_civil_society_profile` tool + panel). Follow-ups: funder rollup, map, ask interface.
- [x] **Phase 6a (depth)** — Deepen what we have rather than adding sources.
  - [x] Ask interface — `/v1/ask` endpoint with Claude tool-use loop, SSE
        streaming, `detect_insights`, `SystemPromptBuilder`, `AskOrchestrator`,
        `ToolDispatcher`, block schema. `/ask` page with `AskBox` +
        `answer_stream.ts`. MCP registration. Live test pending API key in CI.
  - [x] Give Food food banks — `adapters/givefood/` (client + adapter) replacing
        the retired OSM food-bank tag; point-in-polygon counts, map points,
        pre-warming. `get_amenities_geometry` routes each indicator to the
        adapter that owns it. Plan: `docs/plans/2026-06-26-givefood-foodbanks.md`.
  - [x] Neighbourhood granularity — `get_sub_areas` tool + `SubAreaTableBlock`;
        `compare_places` `context_place_ids` for cross-level comparison; system
        prompt teaches LSOA/ward = "neighbourhood"; UI updates.
        Plan: `docs/plans/2026-06-26-neighbourhood-granularity-plan.md`.
- [ ] **Phase 6b (breadth)** — New data sources via the National Data Library.
      **In progress.**
  - [x] Companies House — aggregates-only bulk loader (Economy depth).
        `adapters/companies_house/` (streaming CSV client + per-LTLA
        aggregation). 3 indicators: `economy.active_companies_count`,
        `economy.active_companies_per_1000`, `economy.new_incorporations_12m`.
        Bulk carve-out (API has no area filter — same as Charity Commission);
        reuses the shared `postcodes_io.resolver`. Live schema smoke green.
        Plan: `docs/plans/2026-06-30-companies-house-loader-plan.md`.
  - [x] Green spaces — **FoE Green Space Consolidated** loader (Environment).
        LSOA + LTLA: `area_per_capita`, `access_pct`, `garden_area_per_capita`,
        `deprivation_score`. OGL/OPL via the FoE near-you portal; FK-tolerant
        (2011→2021 LSOA drift). Second LSOA-level dataset after deprivation.
        Bundled with map fixes (peers period, rank colouring, no-data,
        adapter registration). Plan: `docs/plans/2026-06-30-green-spaces-plan.md`.
  - [ ] Green spaces (deferred to map epic) — OS Open Greenspace site polygons
        + Woodland Opportunity. Their payoff is the map overlay; built with the
        interactive-map epic, not as standalone loaders.
  - [ ] Follow-up: load ONS NSPL/ONSPD bulk once to pre-warm
        `geography.postcode` — speeds up Companies House and every other
        postcode-based loader.

- [ ] **Interactive map** (depth) — one shared MapLibre component (inline +
      explorer page): click popups/side panel, combined point+choropleth layers,
      switchable geography levels. Spec:
      `docs/specs/2026-07-01-interactive-map-design.md`. 6 increments; OS Open
      Greenspace + Woodland fold in here.

## Phase 6a: Depth — beautiful presentation, narrative, natural-language query

Shipped. The pivot (2026-05-31) reoriented Phase 6 away from breadth-first
source expansion toward deepening the data we already hold: a natural-language
ask interface, richer presentation, and finer (neighbourhood) granularity.
Delivered on `feat/neighbourhood-granularity` (ask interface, Give Food food
banks, LSOA/ward granularity). Remaining follow-up: enable the `@pytest.mark.live`
ask test in nightly CI once `ANTHROPIC_API_KEY` is in GitHub Secrets.

## Phase 6b: Breadth — Data Source Expansion (NDL)

See `docs/plans/2026-05-24-phase-6-data-sources-plan.md` for the detailed,
NDL-revised plan.

**Goal:** Expand beyond 8 core domains with high-value neighbourhood data,
sourcing through the rebranded National Data Library (data.gov.uk) where listed.

**Revised priority sources (NDL exploration, 2026-06-29):**
|| # | Source | New Domain | NDL Listed? | Status |
||---|--------|------------|-------------|--------|
|| 1 | BEIS Energy Performance (EPC) | Housing | ✅ Land & property | Download verified |
|| 2 | DEFRA Air Quality | Environment | ✅ Environment | New AURN CSV URL |
|| 3 | Land Registry (HPI + Price Paid) | Housing | ✅ Land & property (×2) | URLs verified |
|| 4 | Ofsted (schools + early years) | Education | ✅ People (×2) | Access via NDL |
|| 5 | DfT Road Safety | Safety | ✅ Transport | Access via NDL |
|| 6 | Ofcom Connected Nations | Digital | ❌ Not in NDL | Direct URL needed |
|| 7 | CQC Care Quality | Health | ❌ Not in NDL | Direct from CQC |

**New sources surfaced by NDL (not in original plan):** homelessness, dwelling
stock & vacancies, rents & lettings, transport connectivity, road/rail noise,
forest & woodlands, pupil attendance, social mobility, water quality,
food hygiene ratings.

**Revised target:** ~75–85+ new indicators across 5–6 new domains
(digital, environment, housing-extended, safety, transport, + social).

**UK-wide deprivation (additional):**
|| Source | Coverage | Status |
||--------|----------|--------|
|| Scottish IMD | Scotland | ✅ Works (2020v2) |
|| Welsh IMD 2025 | Wales | ⚠️ ODS 404s |
|| NI Deprivation | N. Ireland | ✅ Works |
|| NDL Deprivation sub-topic | UK-wide? | ✅ Listed — needs investigation |

## Decisions Made

| Decision | Rationale | Date |
|----------|-----------|------|
| Compose ports 5433 / 8001 | Avoid local conflict with another stack on 5432 / 8000. | 2026-05-09 |
| sources.yaml aligned to indicators.yaml IDs | The catalogue is the contract; sources must cover everything it references. | 2026-05-09 |
| BUC fallback chain BUC → BSC → BGC → BFC | Several layers don't publish BUC. Smallest available wins for v1's outline-only display. | 2026-05-10 |
| `httpx.MockTransport` instead of `pytest-vcr` cassettes | Some upstream service URLs/codes are unverified — re-recording cassettes on every URL fix would loop. Live verification runs nightly. | 2026-05-10 |
| Loader runs as a separate `loader` Docker service | Per design §6. Server serves reads, loader runs APScheduler against `refresh_cadence`. Same image, different command. | 2026-05-10 |
| MCP transport via `FastMCP.sse_app()` mounted on FastAPI | Single ASGI process, no separate stdio supervisor. Same tool handlers as the HTTP routes; MCP module is registration boilerplate only. | 2026-05-10 |
| `cache_status` window estimated from cron via `_cron_to_window_days` | Avoids a new dep; falls back to 30 days for unrecognised patterns. Good enough for v1. | 2026-05-10 |
| Local git identity = `Tom Watson <tom@good-ship.co.uk>` | Matches active session email. | 2026-05-09 |
| IMD 2025 + IMD 2019 as parallel sources (`mhclg.imd2025`, `mhclg.imd2019`) | Both editions are useful; 2019 is the established baseline, 2025 is the new release. `fetch_indicator(period=None)` returns latest (2025) by default. Subclassing reuses the parser/loader logic. | 2026-05-11 |
| IMD 2025 uses File 5 (Scores), IMD 2019 uses File 2 | MHCLG restructured the 2025 release: raw scores moved out of File 2 (now rank/decile only) into File 5. IMD 2019 still has scores + deciles in File 2. | 2026-05-11 |
| IMD loader pre-filters rows by existing `geography.place` ids | Avoids FK violations during partial seeds (`make seed-light`) and absorbs minor LSOA boundary version mismatches between IMD editions and our geography spine. | 2026-05-11 |
| `compare_places` defaults `comparison_basis="percentile"` | Spec §4.4 leaves the default open; percentile against same-type peers is the most useful "how does my place compare?" framing for downstream LLMs. | 2026-05-12 |
| Passthrough peer-universe fan-out has soft budget 200 | Compare_places against >200 peers via real upstream calls is wasteful and slow; instead, rank only the caller's slice with a `BUDGET_CAVEAT` so the methodology is explicit. | 2026-05-12 |
| `series_break:` prefix on catalogue caveats | Phase 3 plan Task 2 convention: the prefix partitions caveats into `Trend.breaks_in_series` (prefix stripped) vs the regular caveats list on the response. | 2026-05-12 |
| Police.uk methodology caveat asserted verbatim by adapter test | Centroid-proximate aggregation undercounts large or dispersed LTLAs; a refactor that drops the caveat would silently degrade provenance, so the test pins the exact string. | 2026-05-12 |
| UI uses `linkedom` for SSR DOM polyfill | `@observablehq/plot` calls `document.createElement` internally; Node SSR has no native `document`. linkedom is lighter than jsdom and ships a spec-shaped DOM that Plot is happy with. | 2026-05-12 |
| Phase 3 Block E onwards lands as squash-merged PRs | First three phases shipped as direct commits to `main`. From PR #1 onwards each block of Phase 3 is a feature branch + squashed PR — matches the global "always work on a branch" rule. | 2026-05-12 |
| Phase 6 data source validation approach | URL validation first, then TDD implementation per adapter. Priority ordered by API stability: EPC/Land Registry → DEFRA → CQC → DfT. | 2026-05-24 |
| Phase 6 pivots to depth-first | Stop breadth-first source expansion; deepen the data we hold via a natural-language ask interface, richer presentation, and neighbourhood granularity. | 2026-05-31 |
| Phase 6 runs as two tracks | 6a (depth) shipped — ask interface, Give Food food banks, LSOA/ward granularity. 6b (breadth) resumes source expansion via the National Data Library. Depth shipped first; breadth is the next track. | 2026-06-30 |

## Open Questions

- [ ] OGP service URLs in `docs/adr/0001-geography-data-sources.md` marked
      `(unverified)` — confirm against the live portal at first seed.
- [x] Nomis dataset/measure/cell codes in `catalogue/nomis-mapping.yaml`
      ~~confirm against real API~~ — verified for `population.total` (MYE),
      `population.households.lone_parent_share` (Census), and
      `economy.employment_rate` (APS, NM_17_5 variable 45) via live tests.
      Other Census TS-table dataset IDs (ethnic group, qualifications,
      tenure) and the ONS APS pay + affordability codes are pinned with
      plausible IDs but only exercised at first integration test or tool use.
- [x] IMD 2025 download URL in ADR-0002 — ~~confirm or fall back to 2019~~
      verified live 2026-05-11.
- [ ] IMD 2025 deciles — File 2 (deciles/ranks) is not currently loaded for
      the 2025 edition.
- [ ] LTLA-filter for `make seed-light` — currently filters MYE+Census by
      place_filter; geography spine still loads full layers (minus LSOA/MSOA
      in light mode).
- [ ] Nomis `value_scale` is currently only wired into the Census + APS
      adapters. MYE doesn't need it for `population.total` (count). When a
      future MYE indicator uses `measures=20301` (percent) it'll need the
      same treatment — extract a shared helper at that point.
- [x] **Fingertips data endpoint pattern (Phase 3 Task 11)** — resolved
      2026-05-11.
- [ ] Stat-Xplore cube IDs in `catalogue/statxplore-mapping.yaml` are
      plausible-but-unverified; auth-gated schema. Live test will iterate
      once `STATXPLORE_API_KEY` is added to GitHub Actions Secrets.
- [ ] DfE EES KS4 + persistent absence dataset/indicator UUIDs are
      placeholders; only FSM is real. Live test fails-closed nightly until
      they're discovered.
- [ ] Nomis APS median pay (NM_30_1) + affordability ratio (NM_173_1)
      field codes haven't been live-discovered — employment / unemployment
      use the verified NM_17_5 path. When these indicators get exercised,
      mirror the NM_17_5 discovery (probe `.def.sdmx.json` to find the
      variable + measure codes).
- [ ] Police.uk `crime.violence_rate` and `crime.asb_rate` haven't been
      live-tested individually. Category slugs are stable but a smoke run
      before the Phase 3 tag would be sensible.

## Out of Scope

- Phase 4+ work — separate plan documents.
- Anything in v1.5/v2/v3 — separate spec docs.
