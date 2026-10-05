# Evidence synthesis + map improvements

**Date:** 2026-10-05  
**Status:** In progress

## Goal

Make Soundings better at turning several kinds of place evidence into one useful answer, and make maps clearer when they show need, vulnerability and provision together.

## Slice 1 — evidence synthesis

- Teach Ask to deliberately triangulate four evidence types when the question warrants it:
  1. official indicators;
  2. trend/change over time;
  3. contributed observations from local organisations;
  4. local provision/context (amenities and civil society).
- Require the narrative to distinguish those evidence types rather than flattening them together.
- Ask should explicitly surface:
  - alignment between evidence sources;
  - tensions or contradictions;
  - missing evidence;
  - one or two things worth paying attention to next.
- When a theme has a meaningful spatial provision layer, prefer a combined choropleth + amenity map.
- Do not manufacture a synthesis when a layer is unavailable: say what is missing.

## Slice 2 — map semantics and interaction

- Correct choropleth direction for indicators where lower values mean greater priority, starting with PPFI deciles.
- Make legends explain relative-rank colouring and, where relevant, the meaning of the low/high ends.
- Add a persistent selected-area outline when a choropleth area is clicked.
- Improve explorer side-panel actions so users can move from a selected place into its full profile or Ask.
- Add optional provision overlays in the explorer for selected/drilled areas, starting with food banks, GP practices, schools and parks.

## Verification

- Add prompt tests for triangulation guidance.
- Add pure map-renderer tests for scale direction and legend semantics.
- Run the existing Python lint/type/test suite and UI typecheck/tests in CI.
