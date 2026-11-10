"""Calculation tools: the model narrates sums it did not do.

The 9 November 2026 baseline found synthesis errors in 17 of 30 answers:
counts read as shares, unweighted means of local authority rates presented
as regional rates, differences misstated. These tools do the arithmetic and
refuse operands that no tool returned, so a calculation cannot be used to
launder an invented number.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from soundings.ask.dispatcher import ToolDispatcher
from soundings.ask.grounding import EvidenceSet
from soundings.tools.calculate import (
    UntracedOperandError,
    calculate_difference,
    calculate_share,
    calculate_weighted_average,
)


def _evidence(*values: float) -> EvidenceSet:
    ev = EvidenceSet()
    for v in values:
        ev.add_value(v)
    return ev


def test_share_returns_proportion_percent_and_working() -> None:
    out = calculate_share(
        numerator=6, denominator=10, evidence=_evidence(6, 10), label="LSOAs in decile 1"
    )
    assert out.proportion == pytest.approx(0.6)
    assert out.percent == pytest.approx(60.0)
    assert out.working == "6 / 10 = 0.6 (60%)"


def test_share_refuses_an_operand_no_tool_returned() -> None:
    with pytest.raises(UntracedOperandError, match="8"):
        calculate_share(numerator=8, denominator=10, evidence=_evidence(6, 10))


@pytest.mark.parametrize(("num", "denom"), [(11, 10), (-1, 10), (2, -10)])
def test_share_rejects_impossible_values(num: float, denom: float) -> None:
    with pytest.raises(ValueError, match="share|denominator"):
        calculate_share(numerator=num, denominator=denom, evidence=_evidence(num, denom))


def test_weighted_average_rejects_negative_weights() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        calculate_weighted_average(
            values=[0.2, 0.4], weights=[100, -50],
            evidence=_evidence(0.2, 0.4, 100, -50),
        )


def test_share_refuses_zero_denominator() -> None:
    with pytest.raises(ValueError, match="zero"):
        calculate_share(numerator=6, denominator=0, evidence=_evidence(6, 0))


def test_difference_of_proportions_is_reported_in_percentage_points() -> None:
    out = calculate_difference(value=0.3285, baseline=0.1764, evidence=_evidence(0.3285, 0.1764))
    assert out.difference == pytest.approx(0.1521)
    assert out.percentage_points == pytest.approx(15.21)
    assert out.percent_change == pytest.approx(86.22, abs=0.01)


def test_difference_of_counts_has_no_percentage_points() -> None:
    out = calculate_difference(value=546412, baseline=528155, evidence=_evidence(546412, 528155))
    assert out.difference == pytest.approx(18257)
    assert out.percentage_points is None


def test_rounded_operands_trace_to_the_unrounded_value() -> None:
    """The model may pass 32.8 for a stored 0.32849837."""
    out = calculate_difference(value=32.8, baseline=17.6, evidence=_evidence(0.32849837, 0.17645))
    assert out.difference == pytest.approx(15.2)


def test_weighted_average_differs_from_the_simple_mean_and_says_so() -> None:
    """Three authorities: a big one at 20%, two small ones at 40%."""
    out = calculate_weighted_average(
        values=[0.20, 0.40, 0.40],
        weights=[800_000, 100_000, 100_000],
        evidence=_evidence(0.20, 0.40, 800_000, 100_000),
        weight_label="population",
    )
    assert out.weighted_average == pytest.approx(0.24)
    assert out.simple_mean == pytest.approx(0.3333, abs=1e-4)
    assert out.n == 3
    assert "weighted by population" in out.working


def test_weighted_average_needs_matching_lengths() -> None:
    with pytest.raises(ValueError, match="same length"):
        calculate_weighted_average(values=[0.2], weights=[1, 2], evidence=_evidence(0.2, 1, 2))


# --- through the dispatcher --------------------------------------------------


def _dispatcher() -> ToolDispatcher:
    state = SimpleNamespace(
        geography_service=MagicMock(), orchestrator=MagicMock(), engine=MagicMock()
    )
    return ToolDispatcher(state)


async def test_calculation_results_become_evidence_for_the_answer() -> None:
    dispatcher = _dispatcher()
    dispatcher.evidence.add_value(6)
    dispatcher.evidence.add_value(10)

    result: dict[str, Any] = await dispatcher.dispatch(
        "calculate_share", {"numerator": 6, "denominator": 10}
    )

    assert result["percent"] == pytest.approx(60.0)
    assert dispatcher.evidence.traces(
        __import__("soundings.ask.grounding", fromlist=["extract_figures"]).extract_figures("60%")[
            0
        ]
    )


async def test_dispatcher_surfaces_untraced_operands_as_an_error() -> None:
    dispatcher = _dispatcher()
    with pytest.raises(UntracedOperandError):
        await dispatcher.dispatch("calculate_share", {"numerator": 3, "denominator": 7})


def test_calculation_tools_are_offered_to_the_model() -> None:
    names = {spec["name"] for spec in _dispatcher().tool_specs()}
    assert {
        "calculate_share",
        "calculate_difference",
        "calculate_weighted_average",
        "aggregate_sub_areas",
    } <= names
