"""Numbers in an answer must trace back to a tool result.

The 9 November 2026 baseline averaged 0.97 out of 2 on grounding: answers
carried figures no tool had returned (unweighted means presented as local
rates, counts read as shares). These tests pin the check that catches them.
"""

import pytest

from soundings.ask.grounding import EvidenceSet, extract_figures


def _evidence(*values: float) -> EvidenceSet:
    ev = EvidenceSet()
    ev.add_result({"values": [{"value": v} for v in values]})
    return ev


def test_collects_numbers_from_nested_tool_results() -> None:
    ev = EvidenceSet()
    ev.add_result(
        {
            "place": {"id": "ltla24:E08000032", "name": "Bradford"},
            "values": [{"value": 0.324, "period": "2024/2025"}],
            "parent_value": 546412,
            "partial": False,  # booleans are not figures
        }
    )
    assert ev.supports(32.4, is_percent=True, decimals=1)
    assert ev.supports(546412, is_percent=False, decimals=0)
    assert 0.0 not in ev.values  # the False flag was not collected as a number


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("FSM eligibility is **32.4%**.", [("32.4%", 32.4, True, 1)]),
        ("About 546,412 people live there.", [("546,412", 546412.0, False, 0)]),
        ("Population is 1.2 million.", [("1.2 million", 1_200_000.0, False, -5)]),
        ("A ratio of 7.6 against England's 7.7.", [("7.6", 7.6, False, 1), ("7.7", 7.7, False, 1)]),
        ("Up 3.5 percentage points.", [("3.5 percentage points", 3.5, False, 1)]),
    ],
)
def test_extracts_figures_with_their_precision(
    markdown: str, expected: list[tuple[str, float, bool, int]]
) -> None:
    got = [(f.text, f.value, f.is_percent, f.decimals) for f in extract_figures(markdown)]
    assert got == expected


@pytest.mark.parametrize(
    "markdown",
    [
        "In 2024 and in 2019/20 the series changed.",  # years and periods
        "The 3 most deprived neighbourhoods.",  # small counts of things
        "Decile 1 is the most deprived.",
        "Ranked 5th of 10.",
        "LSOA E01011954 and ward E05001234.",  # codes
        "Census 2021 and IMD 2025.",
        "Q1 to Q4.",
    ],
)
def test_ignores_years_codes_ordinals_and_small_counts(markdown: str) -> None:
    assert extract_figures(markdown) == []


@pytest.mark.parametrize(
    ("statement", "value"),
    [
        ("Unemployment fell by -3.5 percentage points.", -3.5),
        ("Employment increased +2.4%.", 2.4),
        ("The rate is −0.8%.", -0.8),
        ("There are 3 schools.", 3.0),
        ("There are 0 charities.", 0.0),
    ],
)
def test_extracts_signed_figures_and_small_real_counts(statement: str, value: float) -> None:
    assert extract_figures(statement)[0].value == value


def test_percentage_points_cannot_be_laundered_from_proportions() -> None:
    ev = _evidence(0.035)
    assert not ev.traces(extract_figures("Up 3.5 percentage points.")[0])
    ev.add_value(3.5)
    assert ev.traces(extract_figures("Up 3.5 percentage points.")[0])


def test_scoped_evidence_cannot_validate_another_place_or_indicator() -> None:
    ev = EvidenceSet()
    ev.add_result({
        "indicator_key": "education.fsm_eligibility_share",
        "period": "2025",
        "sub_areas": [{"place_id": "lsoa21:A", "value": 0.324}],
        "parent_place_id": "ltla24:X", "parent_value": 0.247,
    })
    assert ev.supports_in_context(
        0.324, indicator_key="education.fsm_eligibility_share",
        place_id="lsoa21:A", period="2025",
    )
    assert not ev.supports_in_context(
        0.324, indicator_key="education.fsm_eligibility_share",
        place_id="lsoa21:B", period="2025",
    )
    assert not ev.supports_in_context(
        0.324, indicator_key="population.total",
        place_id="lsoa21:A", period="2025",
    )
    assert ev.supports_in_context(
        0.247, indicator_key="education.fsm_eligibility_share",
        place_id="ltla24:X", period="2025",
    )


def test_metadata_coordinates_are_not_evidence() -> None:
    ev = EvidenceSet()
    ev.add_result({"lat": 54.3, "coordinates": [32.4, 12.3], "value": 0.247})
    assert not ev.traces(extract_figures("54.3%")[0])
    assert ev.traces(extract_figures("24.7%")[0])


def test_percent_traces_to_a_proportion_within_display_rounding() -> None:
    ev = _evidence(0.32449)
    (fig,) = extract_figures("32.4%")
    assert ev.traces(fig)
    (wrong,) = extract_figures("34.2%")
    assert not ev.traces(wrong)


def test_rounded_counts_trace() -> None:
    ev = _evidence(546412.0)
    assert ev.traces(extract_figures("546,412 residents")[0])
    assert ev.traces(extract_figures("about 546,000 residents")[0])
    assert ev.traces(extract_figures("0.55 million residents")[0])
    assert not ev.traces(extract_figures("650,300 residents")[0])


def test_percent_stated_as_a_percentage_value_also_traces() -> None:
    """Some sources publish 0-100 values (deprivation scores, IMD domains)."""
    ev = _evidence(21.7)
    assert ev.traces(extract_figures("21.7%")[0])


def test_untraced_lists_only_the_unsupported_figures() -> None:
    ev = _evidence(0.324, 0.247)
    md = "Bradford is 32.4%, England 24.7%, and the regional average is 29.1%."
    assert [f.text for f in ev.untraced(md)] == ["29.1%"]
