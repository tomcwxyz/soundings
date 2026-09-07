"""Ask guidance for Soundings' indexed 360Giving grant tools.

Kept separate from the broad place-analysis prompt so the grants capability has
an explicit boundary that can move with ``soundings.grants`` if it is later
extracted into a dedicated service.
"""

GRANTS_GUIDANCE = """\
Grant-query guidance — this takes precedence over older generic grant guidance
elsewhere in the system prompt:

- For headline place-level totals in the last 12 months, use get_indicators with
  civil_society.grants_in_last_12m_total and civil_society.grants_in_last_12m_count.
- Use search_grants when the user wants individual grant evidence or asks which
  grants, funders or recipients match a topic, place, date range or amount.
  Prefer this over reconstructing grant evidence from charity profiles.
- Use get_funder_profile for questions about a specific funder's grantmaking,
  including totals, award range, leading recipients and programmes. If an exact
  360Giving Org ID is known, the tool can refresh that funder from the official
  API before profiling.
- Always inspect index_complete/index_coverage in search_grants and
  get_funder_profile results. If index_complete is false, describe the results
  as the currently indexed subset and NEVER treat an absent grant, recipient or
  funder as evidence that it is absent from the full 360Giving corpus.
- The local grant index is an evidence layer, not a recommendation engine. Base
  claims about funding patterns on returned grants or deterministic aggregates.
- Do not use find_organisations_in_place with funded_only until the full-corpus
  index is wired into that filter.
"""
