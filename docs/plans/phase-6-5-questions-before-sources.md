# Phase 6.5 — Questions before sources

## Why this phase exists

Soundings has enough source breadth that "add another dataset" is no longer a
good default development loop. The next constraint is whether the system can
answer useful place questions clearly, comparatively and honestly.

This phase makes the **question** the unit of product development.

## Baseline

`evaluation/questions.yaml` contains 30 curated questions spanning:

- place summaries and comparisons;
- neighbourhood deprivation;
- population/economy trends;
- health and education;
- housing and crime;
- civil society and grants;
- facilities, green space, air quality and physical activity;
- cross-domain synthesis;
- Scotland/Northern Ireland coverage;
- known derived-measure gaps.

Each case records the tools and active indicators it should be able to use,
the answer forms that are likely to help, success criteria, and whether we
currently expect the question to be supported, partial, or a known gap.

The static checker deliberately requires no Anthropic key:

```bash
cd server
uv run python scripts/check_question_set.py
```

CI validates that question cases do not reference invented tools or retired
indicators, and that the set exercises every non-terminal Ask tool.

## Development loop

For each evaluation round:

1. **Ask the question.**
2. **Classify the failure** before changing code: routing, missing-data,
   geography, upstream, synthesis, presentation, or coverage-honesty.
3. **Fix the smallest underlying capability** that improves the most important
   questions.
4. **Re-run affected questions**, not just unit tests.
5. **Only add a source when a real question requires it.**

The manual scoring rubric lives in `evaluation/rubric.md`.

## Initial hypotheses

The baseline deliberately contains three known gaps:

- Scottish local-authority organisation discovery;
- claimant-count **rate** (the live DWP product exposes a count and needs a
  tested denominator/derivation);
- AHC child-poverty **proportion** (again, the live DWP product exposes a
  count and needs an explicit derivation).

It also marks several partial questions where Soundings should be useful but
coverage/series depth is uneven: housing affordability, broad "what changed?"
questions, family-pressure synthesis, and whole-place summaries in Scotland
and Northern Ireland.

These should compete with each other for priority. They are not an automatic
to-do list.

## Later: consented corpus

The curated set is the starting benchmark. As the public question corpus
grows, recurring consented/sanitised questions should be promoted into this
evaluation set. Raw private answer-cache/history data must not be used as a
cross-user question feed.
