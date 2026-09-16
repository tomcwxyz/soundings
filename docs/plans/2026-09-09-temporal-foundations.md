# Temporal foundations

**Status:** implementation slice
**Date:** 2026-09-09
**Scope:** temporal semantics and question-shaped access over existing Soundings observations. No historical-boundary remapping in this slice.

## Why

Soundings already stores repeated indicator values by `(place_id, indicator_key, period)` and exposes `get_trend`, but `period` is an opaque string and loader-mode trends rely on a second `data.trend_point` table that only some loaders populate.

That is enough for simple sparklines, but it is not a strong enough contract for questions such as:

- what was true here in a particular year?
- how has this indicator changed over a period?
- what is the latest observation and what period does it actually describe?
- can two observations be compared safely when their periods use different labels?

The first temporal slice makes time explicit without breaking existing adapters.

## Principles

1. Keep the source `period` label for backwards compatibility and provenance.
2. Derive a structured temporal extent alongside it.
3. Treat `data.indicator_value` as the canonical loader-mode observation store.
4. Keep `data.trend_point` only as optional trend metadata / compatibility, not as a prerequisite for a trend.
5. Make change a deterministic tool result rather than asking the model to calculate it from prose.
6. Defer historical geography reconciliation to a later slice.
7. Do not infer calendar semantics that the source label does not actually prove.

## T1 — temporal semantics

Add `TemporalExtent` with:

- `label`
- `granularity`: `day | month | quarter | year | financial_year | range | edition | unknown`
- `reference_start`
- `reference_end`
- `edition`

Add a conservative period parser for common Soundings/source labels:

- `YYYY`
- `YYYY-MM`
- `YYYY-MM-DD`
- `YYYY-QN` and `QN YYYY`
- explicit `FY YYYY/YY` financial years
- bare `YYYY/YY` labels as an undated `range`, because they could mean academic, financial or another reporting year
- fallback to `unknown` without inventing dates

`IndicatorValue` and `TrendPoint` expose `temporal`, populated automatically from `period` when not supplied explicitly.

## T2 — canonical observations

For loader-mode `get_trend`:

- read periods and values from `data.indicator_value`;
- left join `data.trend_point` to retain its `revised` flag where present;
- order/filter in Python using parsed temporal extents so mixed period labels do not depend on lexical SQL ordering;
- return all matching observations even when no `trend_point` mirror exists.

This deliberately does **not** remove `data.trend_point` yet. It turns it into optional metadata and avoids a migration/refactor across every loader in one change.

## T3 — query primitives

Existing tools cover:

- latest/exact value: `get_indicators`
- range: `get_trend`

Add `get_change` for one place + indicator:

- optional `period_from` / `period_to` window;
- first and last usable observations in the window;
- absolute change;
- percentage change when the starting value is non-zero;
- direction: `increase | decrease | unchanged`;
- source/caveat propagation from `get_trend`.

Expose the tool through HTTP, MCP and Ask dispatcher. Its tool description tells Ask to prefer deterministic change calculation over model arithmetic for explicit change questions.

## Evaluation

Keep temporal cases in `evaluation/temporal_questions.yaml`, composed into the default Phase 6.5 question set alongside the baseline and grant-specific slice. The cases exercise:

- multi-year population change;
- monthly Universal Credit change;
- explicit IMD edition comparison;
- transparent period coverage and labelling.

Success criteria:

- trend works from `indicator_value` without `trend_point` rows;
- period parser handles common annual/monthly/quarterly labels and explicit financial years;
- ambiguous ranges and unknown labels are preserved without false precision;
- `get_change` returns deterministic arithmetic and preserves provenance;
- existing `period` consumers remain compatible.

## Deferred

- historical `place_hierarchy` validity;
- historical postcode-to-geography mapping;
- formal geography vintage / boundary-mode selection;
- publication/revision timestamps beyond existing provenance;
- replacing `trend_point` physically with a view or removing the table;
- retaining historical snapshots of mutable organisation / GrantNav state.
