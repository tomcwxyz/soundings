# Soundings question evaluation rubric

Phase 6.5 treats questions as the unit of product development. The curated
question set is a baseline for repeated evaluation, not a promise that every
question is currently answered well.

When Ask evaluation is run manually or with an authorised model key, score each
answer from **0–2** on six dimensions:

| Dimension | 0 | 1 | 2 |
| --- | --- | --- | --- |
| Question fit | Misses the question | Partly answers | Directly answers |
| Grounding | Unsupported/fabricated claims | Mixed grounding | Claims trace to Soundings tools/sources |
| Geography | Wrong/misleading level | Mostly appropriate | Correct level and boundaries are explicit |
| Context | No useful comparison/time context | Some context | Comparison/trend matches the question |
| Coverage honesty | Hides gaps/zeros | Caveats are weak | Partial/unavailable coverage is explicit |
| Presentation | Confusing or broken | Usable | Appropriate cards/charts/maps/tables |

A score is useful, but failure classification matters more. Tag failures as one
or more of:

- **routing** — the wrong tool or indicator was selected;
- **missing-data** — the question needs a source/derived measure Soundings lacks;
- **geography** — the requested level cannot be served correctly;
- **upstream** — a source/API contract has drifted or failed;
- **synthesis** — the evidence was available but the answer did not combine it well;
- **presentation** — the evidence was right but the chosen block/chart/map was poor;
- **coverage-honesty** — missing or partial data looked complete.

## Hard failures

Regardless of score, treat these as failures:

- inventing an indicator or value;
- presenting a national slice as local data;
- returning a count under a rate/proportion label;
- silently replacing an unavailable measure with a different one;
- presenting missing coverage as a genuine zero;
- using an unsuitable choropleth or other materially misleading visual.

## How to use this

1. Run the static checker first: `cd server && uv run python scripts/check_question_set.py`.
2. Run/inspect individual questions when model-backed evaluation is intentionally enabled.
3. Record the failure type before proposing a new source or feature.
4. Prefer fixes that improve several questions.
5. Add new sources only when they unlock important questions that the existing stack
   cannot answer honestly.
