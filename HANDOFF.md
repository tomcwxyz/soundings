# Handoff — 29 August 2026

## Where we are

Soundings is no longer at the Phase 5 position described by the old README. Phases 0–5 are complete, Phase 6a's depth work is shipped, and Phase 6b has already delivered several additional data sources.

The consolidation pass is complete. Development has now moved into **Phase 6.5 — Questions before sources**.

### Consolidation completed

- Nightly DB safety/configuration fixed in **PR #31**.
- Main code/test drift fixed in **PR #33**:
  - Charity Commission loader test doubles updated for the newer area-of-operation and classification passes;
  - amenity overlays again reject an empty indicator list;
  - stale civil-society and air-quality expectations aligned with current contracts/methodology;
  - strict mypy issues fixed;
  - follow-up answer rendering made TypeScript-safe without reassigning function declarations;
  - organisation response typing updated for `operates_in_place_names`.
- PR #33 passed the server test job, strict Python lint/mypy and the full UI typecheck/tests before merge.
- The timeout/choropleth protections from old PR #29 are already present in current code, so that PR should be closed rather than merged.
- The corrected nightly was exercised immediately on 29 August. Its first real run exposed five live-integration failures rather than infrastructure failure. The fixes are collected in **PR #36**:
  - DfE query pagination moved into the current POST JSON contract;
  - Police.uk throttling/retry added for 429 responses;
  - Find That Charity moved to its current `/api/v1/charities` direct-lookup endpoint;
  - unsupported FTC Scottish/NI place discovery now fails closed instead of returning misleading results;
  - Ask live smoke receives `ANTHROPIC_API_KEY` when configured and skips cleanly when it is not.
- A subsequent live run passed, and Stat-Xplore was then enabled with a real Actions secret. Its authenticated schema showed that the old UC geography/date IDs were stale. The UC mapping was updated to the current `COA_CODE` / `V_C_MASTERGEOG21_LA_TO_REGION` geography and `F_UC_DATE:DATE_NAME` date field.
- Stat-Xplore latest-value requests now resolve the newest month from schema and query only that month + local authority; requested trend windows are narrowed upstream too.
- The live suite passes with **13 passed and 1 deliberate skip**; the Ask/Anthropic smoke is intentionally being left skipped for now.
- A follow-up authenticated schema probe checked the two remaining speculative Stat-Xplore mappings. The old database IDs were stale; current DWP products are `ACC` (Alternative Claimant Count) and `CILIF_AHC` (AHC Relative Low Income), and both expose count measures. Because Soundings advertised those entries as proportions/rates, the two active catalogue/mapping entries were removed rather than returning a count under the wrong unit. Treat them as potential derived measures for the question-led backlog.

## What Soundings currently is

Think of it as four layers:

1. **Shared place/data infrastructure** — geography spine, catalogue, source adapters and PostGIS.
2. **Question-shaped access** — HTTP and MCP tools for finding, describing and comparing places.
3. **Interpretation/interface** — Ask orchestration plus maps, charts and place pages.
4. **Learning commons** — consent-aware capture and publication of the questions people ask.

That is the useful product boundary. Avoid letting the web UI become the only way to think about Soundings: the underlying place-data/tool layer is at least as important.

## What to do next

Phase 6.5 starts from `evaluation/questions.yaml`: a curated baseline of 30 real place questions with expected supported/partial/gap status, required tools/indicators, likely answer forms and explicit success criteria.

The immediate development loop is:

1. validate the question set statically;
2. inspect/run questions without making the Anthropic smoke a release gate;
3. classify failures as routing, missing-data, geography, upstream, synthesis, presentation or coverage-honesty;
4. fix the smallest reusable capability that improves the most important questions;
5. only add a source when a real question requires it.

The current baseline deliberately includes known gaps for Scottish local-authority organisation discovery, claimant-count rate and AHC child-poverty proportion. The latter two are derived-measure questions: current Stat-Xplore products expose counts, so Soundings must not return those counts under rate/proportion labels.

The evaluation set is a hypothesis/benchmark, not a claim that every question is already answered well. The manual scoring rubric is in `evaluation/rubric.md`.

The nightly infrastructure and configured live integrations have been exercised successfully, including Stat-Xplore. The Anthropic Ask smoke is deliberately skipped for now.

## Operations

The public `soundings` repo should continue to hold reusable application code, schemas, generic Docker/Caddy configuration and public runbooks.

The private `soundings-ops` repo is the place for instance-specific operations and encrypted secrets. Do not move generic infrastructure there simply because it is “ops”.

## Branch discipline

`main` is the canonical source of truth.

A number of old branches are squash-merge artefacts. Do not assume “ahead by 1” means useful unmerged work; compare the actual files first.

Preserve `claude/blog-repo-approach-96xj5i` for now because it contains unique blog/diagram drafts. Treat the NSPL/recovery branches cautiously until their differences are inspected. Other completed/superseded branches can be removed once convenient.
