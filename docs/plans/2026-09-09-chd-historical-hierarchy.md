# CHD historical hierarchy ingestion

**Status:** implementation plan  
**Date:** 2026-09-09  
**Depends on:** temporal geography foundations (#50), current hierarchy hardening (#51), CHD inspection (#53–#54)

## Why

Soundings can now represent dated hierarchy edges and can explicitly distinguish
current-boundary from historical-boundary questions, but production geography
loaders still only populate undated current snapshots.

A live inspection of the ONS Code History Database (June 2026) shows that the
current CSV collection is not shaped like the legacy history schema previously
assumed by the adapter. The ArcGIS item contains three CSVs:

- `ChangeHistory.csv`
- `Changes.csv`
- `Equivalents.csv`

`ChangeHistory.csv` is the useful source for temporal containment. Its live
schema includes `GEOGCD`, `PARENTCD`, `OPER_DATE`, `TERM_DATE`, `ENTITYCD` and
`STATUS`. Samples confirm the direct hierarchy chain includes:

- E01 LSOA → E02 MSOA
- E02 MSOA → local authority
- E05 ward → local authority
- local authorities → E12 region
- E12 region → E92 country

The source therefore gives Soundings dated *direct* parent relationships rather
than requiring us to infer containment from change records.

## Date semantics

Soundings stores half-open intervals: `[valid_from, valid_to)`.

CHD `OPER_DATE` is treated as the first valid day. `TERM_DATE` is treated as the
last valid day and converted to the exclusive end by adding one day. This is
consistent with observed boundary transitions in the live source: for example,
terminated local-government geographies ending on 31 March are followed by
replacement structures effective from 1 April, and 2011/2021 output-area
transitions similarly use the day before the new vintage as the termination
value.

Blank `TERM_DATE` means an open-ended interval.

## Schema change: hierarchy edge identity

The current primary key on `(child_id, parent_id)` is no longer sufficient.
Once CHD evidence is loaded, the same relationship can legitimately exist as:

- an undated current snapshot from OGP; and
- one or more dated historical intervals from CHD.

It can also theoretically disappear and later reappear.

Change `geography.place_hierarchy` to use a surrogate `id` primary key and add:

- a partial unique index on `(child_id, parent_id)` for undated current snapshots;
- a partial unique index on `(child_id, parent_id, valid_from)` for dated edges.

A dated refresh can therefore update `valid_to` when ONS revises a record,
while repeated relationships remain representable when they have different
start dates.

The `current_place_hierarchy` view must select distinct `(child_id, parent_id)`
so parallel undated and currently-valid dated evidence cannot duplicate rows in
current consumers.

## Loader scope

Add `OnsGeographyHistoricalHierarchyLoader` backed by the current CHD ArcGIS
item data URL.

The loader will:

1. download the CHD zip;
2. locate `ChangeHistory.csv` structurally from its live header signature;
3. stream rows rather than materialising the uncompressed CSV;
4. parse operative and termination timestamps;
5. ignore rows without a parent relationship;
6. resolve CHD geography codes to existing canonical Soundings `Place` IDs via
   `geography.place.code`;
7. emit every unambiguous/valid canonical child-parent combination for a CHD
   code pair;
8. upsert dated direct edges keyed by `(child_id, parent_id, valid_from)`,
   updating `valid_to` on refresh;
9. report skipped unresolved codes in loader notes/coverage statistics rather
   than inventing place types.

This first ingestion slice deliberately does **not** create historical `Place`
rows for codes Soundings does not already know. That avoids prematurely mapping
raw CHD entity codes such as E01/E02/E05/E06 into Soundings' vintage-specific
place taxonomy. It still gives useful historical evidence for currently-known
LSOAs, MSOAs, authorities, wards, regions and countries.

## Historical traversal

CHD supplies immediate parent relationships, whereas the existing current OGP
loader stores transitive edges. Historical containment should therefore not
expect CHD to materialise every ancestor pair.

Change `GeographyService.find_containing_places_context(...,
boundary_mode="historical")` to traverse dated edges recursively at the
requested date. This reconstructs LSOA → MSOA → authority → region → country
from direct CHD evidence while keeping the stored source representation honest.

Current-boundary mode continues to use existing current/transitive data, but its
result must be distinct because dated and undated evidence may overlap.

Historical mode continues to exclude wholly undated current edges. A historical
request is partial when there is no dated edge from the starting place covering
the requested date; the mere existence of a parallel undated current snapshot
must no longer make an otherwise evidenced historical result partial.

## CHD archive classification

Update the archive inspector so the live June 2026 schema is first-class:

- classify the `ChangeHistory.csv` header signature explicitly;
- expose it separately from the legacy old/new-code history shape;
- keep `Changes.csv` and `Equivalents.csv` inspectable but do not assign semantic
  meaning to them in this slice.

The existing `CodeChange` model/loader is **not** remapped from `Changes.csv`
here. Its semantics need a separate evidence-led decision; `GEOGCD` /
`GEOGCD_P` should not be assumed to mean old/new replacement without source
documentation.

## Refresh and current-loader interaction

The existing OGP hierarchy loader keeps writing one undated current snapshot per
canonical pair. After the identity migration it should use targetless
`ON CONFLICT DO NOTHING`, allowing the partial unique current-snapshot index to
make refreshes idempotent while not conflicting with dated CHD rows.

CHD dated rows coexist with those snapshots. The current view and current
service path deduplicate overlapping evidence.

## Tests

Add coverage for:

- live `ChangeHistory.csv` header classification;
- timestamp parsing and inclusive `TERM_DATE` → exclusive `valid_to` conversion;
- loader resolution from raw CHD codes to canonical Soundings place IDs;
- unresolved historical codes being skipped rather than invented;
- idempotent dated-edge refresh and `valid_to` revision;
- coexistence of an undated current edge and dated intervals for the same pair;
- current view/service deduplication;
- recursive historical traversal across multiple dated direct edges;
- half-open boundary changes;
- no fallback from historical mode to undated current edges.

## Deliberately deferred

- creating historical/vintage `Place` rows for terminated codes not already in
  Soundings;
- historical boundary geometries;
- historical postcode membership;
- interpreting `Changes.csv` as code lineage;
- interpreting `Equivalents.csv` beyond inspection;
- observation remapping to historical or current boundary vintages.

Those become subsequent temporal-geography slices once this direct CHD evidence
is safely flowing through the existing model.

## Acceptance criteria

- Normal current geography consumers remain unchanged in meaning.
- Current queries do not duplicate places when CHD and OGP evidence overlap.
- Historical queries traverse only dated evidence valid at `as_of`.
- A known current LSOA can resolve through dated MSOA/local-authority/region/
  country relationships where CHD and canonical-place coverage exist.
- CHD rows for unknown historical codes are skipped transparently.
- Re-running the CHD loader is idempotent and can update a revised end date.
- No temporary schema-probe workflow remains in the branch.
- Alembic migration, full Postgres-backed tests, Ruff, strict mypy and UI checks
  are green before merge.
