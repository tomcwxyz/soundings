# Ask evaluation — 9 November 2026

- Run the current production orchestrator, prompt, tools and configured Sonnet 5
  against local live data, with no answer-cache replay or corpus capture.
- Leave production code/configuration unchanged. Keep the grant-index-dependent
  cases out of this run as previously requested.
- Use the approved £5 ceiling. Impose a stricter $5 generation guard, reserving
  each call's padded input count at maximum cache-write pricing plus the full
  production output allowance before starting it. Persist the ledger first.
  Keep uncertain failed-call reservations rather than assuming they were free.
- Anthropic's free token-count endpoint supplies preflight counts. Actual response
  usage supplies the cost estimate. Use standard Sonnet 5 prices and ECB's
  9 November GBP/USD reference cross-rate; retain a 25% allowance for tax/FX.
- Run sequentially, starting with neighbourhoods, Welsh/Scottish coverage and
  known gaps, then cover other non-grant cases until the guard stops the run.
- Save final answer events and tool evidence; omit model thinking blocks.
- Score each completed answer manually against evaluation/rubric.md (six 0–2
  dimensions), record hard failures and classify failure types. A timeout or
  budget-interrupted case is not a completed answer-quality observation.
- Report observed latency/usage/cost, completeness, strengths and priorities;
  distinguish empirical results from the question set's expected statuses.

Pricing: https://platform.claude.com/docs/en/about-claude/pricing
FX: https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.eu.html

## Outcome

Completed 30 answers within budget; Q25 interrupted and nine eligible cases
unattempted. Results: [report](../../evaluation/results/2026-11-09/report.md).

Verification: `make test` passed 478 tests (401 deselected). Relevant pre-commit
checks passed. This documentation-only block did not reseed or restart runtime.
