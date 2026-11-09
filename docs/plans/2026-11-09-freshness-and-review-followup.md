# Freshness and review follow-up implementation plan

Goal: make source freshness truthful, repair the identified refresh gaps, then
verify the remaining map/MCP/Ask findings from the 9 November review.

Use the existing local stack, shared source catalogue and loader contracts.
Do not add dependencies, change statistical meanings, fabricate successful
imports, work on the grant index, push, or merge remote PRs.

## Tasks

- [x] Add regression tests for successful/failed scheduled and one-shot imports;
  record their results in `data.loader_run` through one shared tracking path.
  Scope: `server/soundings/loader/`, seed tracking if reuse requires it,
  `server/tests/test_loader_daemon.py` and focused new tracking tests.
- [x] Make health distinguish missing history, overdue successful runs and
  unavailable configured sources. Exclude test fixtures and manual sign-up from
  scheduled freshness checks. Never infer a complete import from a maximum row
  timestamp. Test the actual database contract and preserve honest degradation.
- [x] Investigate Charity Commission register versus aggregates using existing
  data and upstream schema. Test the reproduced failure before any fix; validate
  against an isolated database before changing live aggregates.
- [x] Validate Companies House November download availability and ONS geography
  endpoints; repair only reproduced contract failures. Run tracked refreshes and
  confirm actual row/coverage changes, retaining useful existing data on failure.
- [x] Run `make test`, lint, type checks, isolated integration tests and hooks;
  review/simplify changes; rebuild/restart local runtime services and check the
  public site. Update PLAN/STATE/HANDOFF with observed results and remaining gaps.
- [ ] Resume morning review: test urban/rural map interaction and payloads,
  external MCP discovery/evidence, and Ask timing/quality. Prepare a bounded
  question evaluation using the actual current question set and rubric. Do not
  begin broad new sources, transport migration or mapping architecture changes
  without evidence from those checks.

Verification: keep tests on a separate database; snapshot live data before
refresh writes. Preserve the pre-deployment backup. Do not clear real data just
to make a freshness warning disappear.

Progress: runtime fixes and all three refreshes are deployed and verified.
Map/MCP hands-on checks and static question validation passed; paid Ask scoring
awaits the user's evaluation budget. See `docs/reviews/2026-11-09-runtime-review.md`.
