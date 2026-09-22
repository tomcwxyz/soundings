# Charity Commission lifecycle facts

**Status:** implementation plan  
**Date:** 2026-09-10  
**Depends on:** temporal observation/query foundations and historical geography work

## Why

Soundings now treats observation periods and geography validity as first-class temporal context, but Charity Commission organisation data is still loaded as a current-state surface. The Commission bulk extract includes both registration status and registration/removal dates; Soundings currently discards non-Registered rows before ingestion, which means its existing civil-society removal cohort cannot be complete.

We do **not** need monthly copies of the whole register to answer useful change-over-time questions. Registration and removal are durable source events. Store those lifecycle facts once, then materialise annual place observations that the existing `get_trend` / `get_change` machinery can query.

## Principles

1. **Do not snapshot the entire organisation register monthly.** Preserve lifecycle facts instead.
2. **Keep `data.organisation` as the existing active/current organisation surface.** Removed charities do not become normal organisation-search results in this slice.
3. **Download the main Charity Commission extract once per load.** Split the streamed main-entry rows into lifecycle facts and active rows in-process.
4. **Treat removal precisely.** `removed_on` means removed from the Charity Commission register; it is not a claim about the exact date an organisation ceased operating.
5. **Do not manufacture historical geography.** Lifecycle events are attributed only when the source postcode can be resolved through Soundings' local postcode spine/cache. Removed-only postcodes must not trigger a mass postcodes.io crawl.
6. **Reuse the temporal observation layer.** Annual registration/removal/net series live in `data.indicator_value`; no specialist lifecycle trend tool is needed.

## Storage

Add `data.organisation_lifecycle`:

- `organisation_id` — stable source-namespaced ID, primary key; deliberately **not** an FK to `data.organisation`, because removed charities may not exist on the active/current surface;
- `name`;
- `status` — source status such as `Registered` / `Removed`;
- `registered_on`;
- `removed_on`;
- `postcode` — source postcode when supplied;
- `registered_address_place_id` — nullable current Soundings LTLA attribution from local postcode evidence;
- `source_id`;
- `retrieved_at`.

This is a compact lifecycle fact table, not a version-history table.

## Charity Commission client

Add `iter_main_charities()` which yields every main-entry row (`linked_charity_number == 0`) regardless of registration status, including lifecycle dates and source status.

Keep `iter_active_charities()` as a compatibility wrapper over `iter_main_charities()` filtering `status == "Registered"`.

The loader uses `iter_main_charities()` directly so the primary extract is downloaded only once.

## Geography attribution

For active rows, reuse the existing postcode resolution flow.

For lifecycle-only/removed rows, resolve postcode against `geography.postcode` locally. Do not call upstream postcodes.io. Full deployments normally have NSPL available, but unresolved or obsolete postcodes remain possible and must be counted in loader notes/caveats.

The resulting lifecycle indicators therefore describe registration/removal events **attributed to the current Soundings LTLA represented by the locally-resolved postcode**, not historical boundary membership or historical operating geography.

## Annual observations

Add catalogue indicators:

- `civil_society.charities_registered_count`
- `civil_society.charities_removed_count`
- `civil_society.charities_net_change`

All are annual counts at `ltla24`, source `charity_commission`.

After a successful lifecycle upsert, rebuild these indicator rows from `data.organisation_lifecycle`:

- registrations grouped by `registered_on` year + resolved place;
- removals grouped by `removed_on` year + resolved place;
- net = registrations - removals for each place/year represented by either event stream.

Replace the complete set of these three source-derived indicator rows transactionally on each full load, rather than upserting only positive rows. This prevents corrected source dates from leaving stale historical periods behind.

No writes to `data.trend_point`: the canonical trend path now reads `data.indicator_value`.

## Existing civil-society profile

This slice should stop presenting the active-only organisation table as evidence for historical removals. Update the registration/removal cohort query to use `data.organisation_lifecycle` where lifecycle place attribution exists. Current totals, income distribution, notable organisations and current organisation search remain backed by the active/current tables.

Add a caveat to the profile when lifecycle geography coverage is incomplete if that can be surfaced cheaply from the stored facts; otherwise document the lifecycle-indicator caveat in the catalogue and keep the profile change narrowly factual.

## Question-led evaluation

Add Q38 to the temporal slice, e.g.:

> Are more charities being registered or removed in Gateshead over the last five years?

Require `find_place` + `get_trend`, with both registration and removal indicators. Success criteria must:

- compare source registration and register-removal events over named years;
- avoid calling removal an exact closure/cessation date;
- preserve the registered-address/current-LTLA attribution caveat;
- state partiality if historical postcode coverage is incomplete.

## Tests

Cover:

- client emits Registered + Removed main rows while excluding linked subsidiaries;
- existing active iterator still returns only Registered rows;
- lifecycle model/migration;
- active rows land in both current organisation and lifecycle storage;
- removed rows land only in lifecycle storage;
- removed-only postcode resolution is local-only;
- registration/removal/net annual observations are correct;
- rebuild removes stale lifecycle observation periods after a corrected source date;
- lifecycle upsert is idempotent;
- `get_trend` can retrieve the resulting annual series;
- civil-society cohort removals come from lifecycle facts rather than the active-only table;
- Q38 validates against the catalogue and tool set.

## Deliberately deferred

- monthly organisation snapshots;
- name/address/classification change history;
- historical area-of-operation membership;
- historical postcode-to-boundary reconstruction;
- Scotland/Northern Ireland lifecycle parity;
- a specialist organisation-history API/tool;
- interpreting register removal as organisation closure.

## Acceptance criteria

- Soundings retains source lifecycle facts for removed as well as registered Charity Commission main entries.
- Existing current organisation search semantics remain unchanged.
- Removed-only rows do not cause upstream postcode lookups.
- Annual registration/removal/net series are deterministic, idempotent and queryable through the existing temporal tools.
- Corrected lifecycle dates cannot leave stale indicator periods behind.
- The civil-society profile no longer derives removal cohorts from an active-only dataset.
- Temporal evaluation includes a real charity-lifecycle question.
- Full Postgres-backed tests, Ruff, formatter, strict mypy and UI checks are green before merge.
