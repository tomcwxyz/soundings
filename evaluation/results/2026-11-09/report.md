# Ask answer-quality evaluation — 9 November 2026

Thirty answers completed from forty eligible questions. The budget guard interrupted
Q25 before a final answer; nine eligible questions were not attempted. Q14, Q31,
Q32 and Q44 were excluded because grant-index work is outside this review.

The manual rubric median was **9/12** (mean 8.77). Three completed answers had a
rubric hard failure: Q17 invented an unsupported joint measure; Q26 and Q27
propagated population counts as density. This is a purposeful, budget-limited
sample, not an estimate of overall product accuracy. There is no validated pass
threshold. A completed response does not establish correct data or sufficient
coverage.

## Method and budget

The production `claude-sonnet-5` orchestrator and tool dispatcher ran against the
live local database at application revision `b9f8f62`. No application code,
prompt or configuration was changed. The standalone runner used fresh answers,
no answer-cache replay, no corpus capture and no background-worker lifespan.
Source caches remained shared: this was not a cold-cache benchmark. Production
limits were retained: twelve iterations, 16,000 output tokens, adaptive thinking
and a 180-second timeout.

Priority cases were Q03, Q26, Q28, Q29, Q30, Q01, Q02, Q05, Q23, Q24, Q27,
Q39 and Q43, followed by the remaining questions in numerical order. Saved final
structured blocks and tool evidence were scored manually against the six 0–2
dimensions in `evaluation/rubric.md`. Presentation scores concern block choices
and contents; generated answers were not individually verified in the browser.

The approved ceiling was £5. A stricter **US$5** guard reserved padded input at
maximum cache-write pricing and the complete output allowance before every
model call. Free token-count preflights informed reservations; there were no
uncertain failed-call charges. Q25 was stopped because another full response
could not be reserved, even though settled spending remained below the cap.

Estimated provider usage cost, including Q25's partial work, was **US$4.5139945**,
about **£3.36**. Conservative guarded spending was US$4.542037, or **£4.23** after
a 25% tax/FX allowance. These are estimates, not an invoice. Pricing used Sonnet 5
standard input/output at $2/$10 per million tokens, five-minute cache writes at
$2.50, cache reads at $0.20, and a $4 maximum write rate for reservations.
See [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
and [free token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting).
The GBP/USD cross-rate was 0.83430/1.1193, using the
[ECB reference rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.eu.html)
for 9 November.

Completed-answer latency was **32.9 seconds median**, **96.0 seconds p90**.
This includes the evaluation's free preflight requests, which production does
not make. Source-cache conditions were not controlled. Longer cases included
Q21 (107.9 seconds), Q27 (103.8), Q01 (97.3), Q24 (95.8) and Q23 (95.3).
No before/after speed improvement is claimed.

## Repair priorities

### 1. Correct measures and source contracts before expanding coverage

- **Population density:** all 5,920 stored paired total/density observations
  checked had identical values. Glasgow's 650,300 and Belfast's 352,390 were
  presented as density-based rankings in Q26/Q27. These are upstream unit and
  denominator failures, not model fabrication. Correct the mapping/derivation
  and refresh or withdraw the invalid series.
- **Employment/unemployment cache collision:** the APS key in
  `server/soundings/adapters/ons_aps/adapter.py` includes dataset, measures and
  place, but omits `extra_params.variable`. Employment variable 45 and
  unemployment variable 84 therefore collide for NM_17_5/20599. Q24 repeated
  unemployment values around 66–75%; Q06 and Q21 instead noticed implausible
  data and rejected it. Separate query dimensions, invalidate affected caches
  and test the two series together. Code establishes the collision; not every
  historical value has been individually audited.
- **Other unit contracts:** Q23 correctly rejected an affordability ratio of
  255,734. Pay metadata conflicts between generic values and GBP per week;
  education series use percentage-scale values with a proportion unit. Verify
  upstream fields and denominators before interpretation. No chart multiplying
  these education values by 100 was confirmed: the current renderer plots the
  supplied raw points.

### 2. Restore final-answer provenance

Every completed answer emitted an empty final `sources` list despite source
references being available in some tool payloads. The dispatcher initializes
`_sources` but does not populate it. `get_sub_areas` also omits source/caveat
fields in its returned construction. Preserve and collect evidence through the
full answer path so readers can check source, period, geography and caveats.
This finding concerns the final source list, not the absence of all raw evidence.

### 3. Tighten synthesis and visual semantics

- Define justified similar-place cohorts. A national set of all authorities is
  not automatically a similar-place comparison. Different indicators also use
  different country universes; avoid describing England-only or England/Wales
  distributions as UK-wide.
- Preserve denominators: age counts are not population shares; unweighted
  neighbourhood means are not population-weighted local percentages; a
  percentile is not a percentage of residents. Q30 omitted the unweighted
  IDACI aggregation caveat. Registration date is not foundation date, and
  current-register cohorts are not all historical registrations.
- Remove unsupported spatial clustering, causal and rank claims. Q17 assigned
  effectively zero area per person to ten zero-access neighbourhoods although
  the area-per-person query returned five different places. This establishes
  unsupported joint values, not proof that those true values are nonzero.
  Q10's claim of growth every year contradicts a decline in its own series.
- Preserve filters or send actual data with structured blocks. Q15's
  organisations block omitted the food-activity filter, so client hydration can
  fetch generic charities instead of the subset discussed in prose. Overlapping
  cause categories also require explicit non-exclusive denominators.

### 4. Then reduce latency and rerun the same cases

Inspect repeated tool calls and large profile/tool payloads before changing
models or architecture. The slowest cases and usage ledger identify where to
start. Validate changes with the same questions, including formerly interrupted
and unattempted cases, under a separately approved paid budget. Broader sources,
MCP Apps and map architecture are not required to resolve the demonstrated
correctness issues.

## What worked

The answers often acknowledged unavailable exact measures instead of inventing
claimant or after-housing-cost child-poverty rates. Several correctly rejected
implausible upstream data. Neighbourhood tables, comparisons, registered-office
caveats and explicit modelled/static-data limitations frequently helped. Scottish
and Northern Irish answers still require precise coverage and unit handling.

## Artifacts and limits

- `scores.csv`: all 44 baseline cases, including exclusions and budget statuses.
  Per-case `cost_usd` uses conservative guarded rates; actual provider estimates
  are available per call in `usage.json`.
- `scores.json`: detailed manual findings and six-dimension scores for every
  completed answer. The budget-interrupted case has no quality score.
- `usage.json`: per-call usage, reservations, latency and cost estimates.
- Full sanitized answer/tool records remain locally at
  `/private/tmp/soundings-ask-evaluation-20261109`; model thinking was removed.

The evaluation itself made no repairs, redeployments or paid reruns. The prior
local data repairs and deployment remain in place. Grant-index work remains
excluded. Scores are reviewer judgments and should be read alongside the
specific evidence, not treated as an automated acceptance gate.
