"""Arithmetic the model must not do in its head.

Shares, differences and averages across places are where the 9 November
2026 baseline went wrong most often. These tools do the sums and show the
working. Every operand must be a number some tool returned earlier in the
same conversation (allowing for the rounding it was passed with), so the
tools cannot turn an invented figure into a "calculated" one.

They run inside the Ask dispatcher, which holds the conversation's evidence.
"""

from __future__ import annotations

from math import isfinite

from pydantic import BaseModel, Field

from soundings.ask.grounding import EvidenceSet, shown_decimals


class UntracedOperandError(ValueError):
    """An operand was not returned by any tool in this conversation."""


def _require_traced(evidence: EvidenceSet, **operands: float) -> None:
    missing = [
        f"{name}={value:g}"
        for name, value in operands.items()
        if not evidence.supports(value, is_percent=True, decimals=max(0, shown_decimals(value)))
    ]
    if missing:
        raise UntracedOperandError(
            "OPERAND_NOT_FROM_A_TOOL: "
            + ", ".join(missing)
            + ". Pass values exactly as a tool returned them; fetch the value first if needed."
        )


# --- share --------------------------------------------------------------------


class CalculateShareInput(BaseModel):
    numerator: float
    denominator: float
    label: str | None = Field(
        default=None, description="What is being counted, e.g. 'LSOAs in decile 1'."
    )


class CalculateShareOutput(BaseModel):
    proportion: float
    percent: float
    label: str | None = None
    working: str


def calculate_share(
    *, numerator: float, denominator: float, evidence: EvidenceSet, label: str | None = None
) -> CalculateShareOutput:
    _require_traced(evidence, numerator=numerator, denominator=denominator)
    if not all(isfinite(v) for v in (numerator, denominator)):
        raise ValueError("Share operands must be finite numbers.")
    if denominator <= 0:
        raise ValueError("Cannot take a share: denominator must be positive and non-zero.")
    if numerator < 0 or numerator > denominator:
        raise ValueError("A share requires a numerator between zero and the denominator.")
    proportion = numerator / denominator
    return CalculateShareOutput(
        proportion=proportion,
        percent=proportion * 100,
        label=label,
        working=f"{numerator:g} / {denominator:g} = {proportion:.4g} ({proportion * 100:.4g}%)",
    )


# --- difference ---------------------------------------------------------------


class CalculateDifferenceInput(BaseModel):
    value: float = Field(description="The later or focal value.")
    baseline: float = Field(description="The earlier or comparison value.")
    label: str | None = None


class CalculateDifferenceOutput(BaseModel):
    difference: float
    # Only when both values are proportions (0-1): the difference as
    # percentage points, which is how a change in a rate should be stated.
    percentage_points: float | None = None
    # Relative change, (value - baseline) / baseline, as a percentage.
    percent_change: float | None = None
    label: str | None = None
    working: str


def calculate_difference(
    *, value: float, baseline: float, evidence: EvidenceSet, label: str | None = None
) -> CalculateDifferenceOutput:
    _require_traced(evidence, value=value, baseline=baseline)
    if not all(isfinite(v) for v in (value, baseline)):
        raise ValueError("Difference operands must be finite numbers.")
    difference = value - baseline
    both_proportions = 0 <= value <= 1 and 0 <= baseline <= 1
    points = difference * 100 if both_proportions else None
    percent_change = difference / baseline * 100 if baseline != 0 else None
    working = f"{value:g} - {baseline:g} = {difference:.4g}"
    if points is not None:
        working += f" ({points:+.4g} percentage points)"
    if percent_change is not None:
        working += f"; relative change {percent_change:+.4g}%"
    return CalculateDifferenceOutput(
        difference=difference,
        percentage_points=points,
        percent_change=percent_change,
        label=label,
        working=working,
    )


# --- weighted average -----------------------------------------------------------


class CalculateWeightedAverageInput(BaseModel):
    values: list[float] = Field(min_length=1, max_length=500)
    weights: list[float] = Field(
        min_length=1,
        max_length=500,
        description="One weight per value, usually each place's population.",
    )
    weight_label: str = Field(default="population")
    label: str | None = None


class CalculateWeightedAverageOutput(BaseModel):
    weighted_average: float
    simple_mean: float
    n: int
    label: str | None = None
    working: str


def calculate_weighted_average(
    *,
    values: list[float],
    weights: list[float],
    evidence: EvidenceSet,
    weight_label: str = "population",
    label: str | None = None,
) -> CalculateWeightedAverageOutput:
    if len(values) != len(weights):
        raise ValueError("values and weights must be the same length.")
    _require_traced(
        evidence,
        **{f"values[{i}]": v for i, v in enumerate(values)},
        **{f"weights[{i}]": w for i, w in enumerate(weights)},
    )
    if any(not isfinite(v) for v in [*values, *weights]):
        raise ValueError("Weighted-average operands must be finite numbers.")
    if any(w < 0 for w in weights):
        raise ValueError("Weights must be non-negative.")
    total_weight = sum(weights)
    if total_weight <= 0:
        raise ValueError("Cannot weight by zero: the weights sum to zero.")
    weighted = sum(v * w for v, w in zip(values, weights, strict=True)) / total_weight
    simple = sum(values) / len(values)
    return CalculateWeightedAverageOutput(
        weighted_average=weighted,
        simple_mean=simple,
        n=len(values),
        label=label,
        working=(
            f"Average of {len(values)} values weighted by {weight_label}: {weighted:.4g}. "
            f"The simple mean would be {simple:.4g}; quote the weighted figure for an area "
            "made up of these places."
        ),
    )


# --- specs ----------------------------------------------------------------------


def tool_specs() -> list[dict[str, object]]:
    return [
        {
            "name": "calculate_share",
            "description": (
                "Divide one tool-returned number by another and get the proportion and "
                "percentage, with the working. Use it for any 'X of Y' or 'what share' "
                "statement, e.g. how many of a place's neighbourhoods are in decile 1. "
                "Both numbers must be values a tool returned in this conversation."
            ),
            "input_schema": CalculateShareInput.model_json_schema(),
            "output_schema": CalculateShareOutput.model_json_schema(),
        },
        {
            "name": "calculate_difference",
            "description": (
                "Subtract a baseline from a value, both returned by tools, and get the "
                "difference, the change in percentage points when both are proportions, "
                "and the relative change. Use it for any 'up by', 'above England by' or "
                "'since 2015' statement."
            ),
            "input_schema": CalculateDifferenceInput.model_json_schema(),
            "output_schema": CalculateDifferenceOutput.model_json_schema(),
        },
        {
            "name": "calculate_weighted_average",
            "description": (
                "Average values across several places weighted by a second value "
                "(usually population), both returned by tools. Use it whenever you "
                "describe a group of places as one area: a simple mean of local rates "
                "is not the area's rate."
            ),
            "input_schema": CalculateWeightedAverageInput.model_json_schema(),
            "output_schema": CalculateWeightedAverageOutput.model_json_schema(),
        },
    ]
