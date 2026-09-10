# CHD historical place identities

**Status:** implementation plan  
**Date:** 2026-09-10  
**Depends on:** historical CHD hierarchy ingestion (#55)

## Why

Soundings can now ingest dated CHD hierarchy edges, but #55 deliberately resolves
those edges only when both geography codes already exist in `geography.place`.
That protects the current place spine, but it leaves many terminated historical
geographies unresolved.

The next slice should make extinct ONS/GSS geography codes first-class enough to
support historical containment without pretending they belong to today's
vintage-specific Soundings types.

## Identity principle

Keep all existing current IDs unchanged:

- `lsoa21:<code>`
- `msoa21:<code>`
- `ltla24:<code>`
- `utla24:<code>`
- `ward24:<code>`
- `region:<code>`
- `country:<code>`
- `westminster_constituency_24:<code>`

For CHD codes that do **not** already have a canonical Soundings place, create a
source-faithful historical identity:

- `type = "gss_<entity-code-lower>"`, for example `gss_e07`
- `id = "gss_e07:<gss-code>"`
- `code = <gss-code>`
- `name = GEOGNM`
- `valid_from = earliest OPER_DATE observed for the code`
- `valid_to = exclusive end derived from the latest valid TERM_DATE`, or null if
  the code has a genuinely open-ended CHD record

This preserves the source entity code without claiming that an E01 record is
specifically a 2001, 2011 or 2021 Soundings LSOA type. ONS/GSS entity codes are
geography-type identities; Soundings' existing `lsoa21` etc. remain the
user-facing/current-vintage types.

## No duplicate shadow rows for current places

The historical-place loader must load after the normal OGP place loader and must
not create a `gss_*` shadow row when `geography.place.code` already contains the
GSS code.

This keeps `code -> place` resolution unambiguous for the hierarchy loader and
avoids duplicate current/historical IDs for the same live geography.

If a later current OGP refresh introduces a code that was previously present
only as `gss_*`, reconciliation should be handled explicitly in a later migration
rather than silently creating both identities. The normal refresh order makes
that an edge case rather than the normal path.

## Historical-place loader

Add `OnsGeographyHistoricalPlacesLoader` using the same live CHD
`ChangeHistory.csv` source as the historical hierarchy loader.

The loader will:

1. inspect the CHD archive structurally;
2. stream `ChangeHistory.csv`;
3. reject rows whose supplied operative or termination dates cannot be parsed;
4. require `GEOGCD`, `GEOGNM`, `ENTITYCD` and a valid `OPER_DATE`;
5. aggregate repeated rows for the same code into one place lifetime;
6. retain the latest name by operative date;
7. preserve a null `valid_to` if any valid record for the code is open-ended;
8. skip codes already represented in the canonical place spine;
9. upsert missing `gss_*` place rows idempotently;
10. report source rows, invalid rows, existing-code skips and rows written.

## CHD download efficiency

Do not permanently download the ~36 MB CHD archive twice during one geography
refresh.

Introduce a small CHD archive fetch helper and make the combined
`ons.geography` refresh fetch the archive once, then pass the same bytes to:

1. historical-place ingestion; and
2. historical-hierarchy ingestion.

The individual loaders should keep their direct `load()` methods for isolated
runs/tests, but the normal combined refresh should share the downloaded archive.

## Historical name resolution

Adding extinct places to `geography.place` changes fuzzy search behaviour unless
we make time explicit.

Update `GeographyService.find_place_by_name` so:

- normal calls return only places valid today (including current rows whose
  validity is null/unbounded);
- an optional `as_of` date filters places to those valid on that date.

Expose `as_of` on the `find_place` tool for **name** queries. Historical postcode
resolution remains out of scope because the postcode table is explicitly a
current-vintage snapshot.

A postcode query with `as_of` should therefore continue resolving current
postcode geography and should not imply historical postcode membership.

## Search ranking

`gss_*` types have no modern hierarchy-depth ranking. Leave them at the existing
unknown-type fallback rather than inventing a depth from `ENTITYCD` in this
slice. Similarity remains the primary ranking signal.

## Tests

Add coverage for:

- historical place type/ID construction from `ENTITYCD`;
- aggregation of repeated CHD rows for one code;
- inclusive termination date to exclusive `valid_to` conversion;
- malformed non-empty termination dates being rejected;
- existing canonical codes not receiving `gss_*` shadows;
- idempotent historical-place upsert;
- historical hierarchy resolving through newly-created extinct places;
- default name search excluding terminated historical places;
- `as_of` name search including the correct historical place and excluding
  places not valid on that date;
- `find_place` tool schema and name-query routing with `as_of`;
- one-download behaviour in the combined geography refresh.

## Deliberately deferred

- historical boundary geometries;
- historical postcode membership;
- current-boundary remapping of historical observations;
- interpretation of CHD `Changes.csv` lineage semantics;
- 2011↔2021 LSOA correspondence/remapping;
- UI labels for every GSS entity code;
- automatic migration from an existing `gss_*` identity to a later current OGP
  identity.

## Acceptance criteria

- Existing current place IDs and current search results keep their meaning.
- Extinct CHD codes can exist as dated `geography.place` rows without guessed
  modern vintages.
- The historical hierarchy loader can resolve and persist edges involving those
  extinct places.
- Default name search does not surface expired places.
- `find_place(..., as_of=<date>)` can deliberately resolve a historical name.
- Normal `ons.geography` refresh downloads CHD once for both historical place and
  hierarchy ingestion.
- Full Postgres-backed tests, Ruff, formatter, strict mypy and UI checks are green
  before merge.
