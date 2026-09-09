"""Ask guidance for Soundings' temporal query tools."""

TEMPORAL_GUIDANCE = """\
Temporal-query guidance — use deterministic temporal tools rather than asking
the language model to infer chronology or calculate change itself:

- get_trend returns the ordered observations for one indicator at one place.
  Use it when the user wants the shape of a series or a trend chart.
- get_change calculates first-to-last change over an optional period window and
  returns absolute change, percentage change, direction and provenance.
- For explicit change questions such as "how much has X changed?", "increase or
  decrease?", or "between 2020 and 2024", prefer get_change. Use get_trend as
  supporting evidence or for a chart when the full series adds value.
- Do not calculate change in prose from tool values when get_change can answer it.
- Respect partial results and temporal caveats. If periods cannot be ordered
  safely or a documented series break crosses the requested interval, surface
  that limitation rather than inventing a comparison.
"""
