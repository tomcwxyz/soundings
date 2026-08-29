# Handoff — 29 August 2026

## Where we are

Soundings is no longer at the Phase 5 position described by the old README. Phases 0–5 are complete, Phase 6a's depth work is shipped, and Phase 6b has already delivered several additional data sources.

The 29 August work is a **consolidation pass**, not a feature phase.

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
- A subsequent live run passed: **12 passed, 2 skipped, 648 deselected**. The skips were Stat-Xplore and Ask, both credential-gated in the current Actions environment.

## What Soundings currently is

Think of it as four layers:

1. **Shared place/data infrastructure** — geography spine, catalogue, source adapters and PostGIS.
2. **Question-shaped access** — HTTP and MCP tools for finding, describing and comparing places.
3. **Interpretation/interface** — Ask orchestration plus maps, charts and place pages.
4. **Learning commons** — consent-aware capture and publication of the questions people ask.

That is the useful product boundary. Avoid letting the web UI become the only way to think about Soundings: the underlying place-data/tool layer is at least as important.

## What to do next

The next product work should be chosen from real usage rather than automatically continuing the old source-expansion list.

Likely candidates:

- improve the interactive map and cross-layer exploration;
- deepen high-value questions where current data is weak;
- selectively add Phase 6b sources that unlock those questions;
- improve corpus/public-learning presentation;
- expose/use the MCP/API layer more explicitly outside the Soundings website.

The nightly infrastructure and currently configured live integrations have now been exercised successfully. Before a new feature burst, decide whether to add the missing Stat-Xplore/Anthropic GitHub secrets and whether Scottish/NI organisation place discovery is important enough to source properly.

## Operations

The public `soundings` repo should continue to hold reusable application code, schemas, generic Docker/Caddy configuration and public runbooks.

The private `soundings-ops` repo is the place for instance-specific operations and encrypted secrets. Do not move generic infrastructure there simply because it is “ops”.

## Branch discipline

`main` is the canonical source of truth.

A number of old branches are squash-merge artefacts. Do not assume “ahead by 1” means useful unmerged work; compare the actual files first.

Preserve `claude/blog-repo-approach-96xj5i` for now because it contains unique blog/diagram drafts. Treat the NSPL/recovery branches cautiously until their differences are inspected. Other completed/superseded branches can be removed once convenient.
