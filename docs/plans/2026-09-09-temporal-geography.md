# Temporal geography foundations

**Status:** implementation slice  
**Date:** 2026-09-09  
**Depends on:** Temporal Soundings foundations (PR #49)

## Why

Soundings can now represent observation time, trends and deterministic change,
but its geography hierarchy has historically been timeless. `Place` already
carries `valid_from` / `valid_to`, and `CodeChange` records boundary events,
while `PlaceHierarchy(child_id, parent_id)` has no validity interval.

That means a question such as “which local authority contained this area in
2018?” can only resolve through today's hierarchy. The dangerous failure mode
is not an explicit gap; it is silently answering a historical question with a
current boundary.

## This slice

### Dated hierarchy edges

Add nullable `valid_from` and `valid_to` to `geography.place_hierarchy` plus an
index on `(child_id, valid_from, valid_to)`.

Intervals are **half-open**: `[valid_from, valid_to)`.

Existing null/null edges remain valid current snapshots. Critically, null/null
does **not** mean “valid for all history”. It means the relationship has not yet
been given temporal evidence.

### Boundary modes

`GeographyService.find_containing_places_context()` supports:

- `current_boundary` — use hierarchy edges valid today, including existing
  undated current snapshots. An optional `as_of` is observation context only;
  it does not change the boundary selection.
- `historical` — requires `as_of`; use only dated edges valid on that date.
  Completely undated current edges are excluded.

Historical mode never silently falls back to current containment. If dated
coverage is absent or current undated edges indicate incomplete historical
coverage, the result is marked `partial` and carries an explicit caveat.

The original `find_containing_places(place_id)` API remains available and now
means current-boundary containment explicitly, preserving existing callers.

## What this unlocks

This gives later tools a safe contract for questions such as:

- What authority contained this neighbourhood in 2018?
- Compare an indicator using the boundaries in force at each observation date.
- Compare historical values using today's boundary definition, while stating
  that current-boundary mode is being used.

It also gives archived ONS lookup ingestion somewhere honest to store hierarchy
validity once those archived sources are wired in.

## Deliberately deferred

- **Historical postcode lookup.** `geography.postcode` still represents a
  current resolved postcode row; it is not yet a postcode-to-place history.
- **Historical polygon lookup.** Point-in-polygon continues to use the stored
  current geometry. Historical boundary geometries need a separate decision
  because `Place.id` is currently the primary key even though `Place` already
  has validity columns.
- **Repeated relationship intervals.** The existing composite primary key on
  `(child_id, parent_id)` means one child-parent pair can currently carry one
  continuous validity interval. A rare A→B→A relationship would need a
  surrogate hierarchy-edge key or range-based identity. Do that with archived
  hierarchy ingestion rather than changing identity speculatively here.
- **Automatic archived ONS hierarchy ingestion.** Current ONS lookup chains are
  snapshots and remain undated until their effective interval is verified.
- **Observation-to-boundary remapping.** This slice provides the geography
  contract; remapping/aggregation belongs in a later analytical layer.

## Acceptance criteria

- Existing current-boundary hierarchy queries keep working.
- Expired dated edges are excluded from current containment.
- Historical containment selects the edge valid on the requested date.
- The transition date respects half-open interval semantics.
- Undated current edges are never treated as historical evidence.
- Historical gaps are returned as partial/caveated rather than silently using
  current boundaries.
- Normal migrations, server tests, strict typing and UI checks remain green.
