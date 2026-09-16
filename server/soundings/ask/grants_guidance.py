"""Specialised Ask guidance appended to the broad place-analysis prompt.

Grant guidance remains isolated so that capability can move with
``soundings.grants`` if it is later extracted. Temporal guidance is composed
alongside it because both are appended to every Ask run after the core prompt.
"""

from soundings.ask.temporal_guidance import TEMPORAL_GUIDANCE

_GRANTS_GUIDANCE = """\
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
- find_organisations_in_place(funded_only=true) is authoritative only when the
  result is not partial. Before full GrantNav coverage, or for currently
  unsupported Find That Charity-backed geographies, respect the returned caveat
  rather than treating the organisation list as a complete funded-only result.
"""

# Compatibility name used by AskOrchestrator for the specialised guidance suffix.
GRANTS_GUIDANCE = _GRANTS_GUIDANCE + "\n\n" + TEMPORAL_GUIDANCE
