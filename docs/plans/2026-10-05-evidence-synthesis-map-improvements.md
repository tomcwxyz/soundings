# Evidence synthesis + map improvements

**Date:** 2026-10-05  
**Status:** In progress — slices 1–3 implemented in PR #62

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


## Slice 3 — stateful explorer map

- Add a persistent `InteractiveMap` wrapper for the explorer while keeping the
  existing stateless Ask/place renderers unchanged.
- Swap choropleth GeoJSON and paint state in place rather than recreating
  MapLibre on indicator changes.
- Preserve camera position when the geography context is unchanged.
- Refit only when moving between national and focused-authority contexts.
- Preserve and clear selected-area state explicitly.
- Update provision point layers in place as checkboxes change.
- For national LSOA views such as PPFI, resolve the clicked neighbourhood's
  containing authority and offer a direct "Focus on neighbourhoods in …" route.
- Add unit coverage for prepared choropleth state, including reversed PPFI rank
  semantics and non-mutating GeoJSON preparation.


## Slice 4 — neighbourhood comparison

- Let users build a comparison set of up to five LSOAs from a focused authority
  without introducing a separate map mode.
- Keep the active area highlighted in navy and the comparison set highlighted
  separately on the same persistent map.
- Preserve a comparison set when switching to another LSOA-capable indicator in
  the same focused authority.
- Clear the set when the geography context is no longer compatible.
- Link the selected set into the existing Compare page using absolute values for
  the active indicator.
- Provide an "Ask about selected" route that names the chosen neighbourhoods,
  their parent authority and the active indicator so Ask can investigate the set
  rather than starting from an unscoped question.
