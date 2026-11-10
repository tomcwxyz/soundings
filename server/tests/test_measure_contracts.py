"""Measure contracts: every indicator states what kind of number it is.

The 9 November 2026 answer evaluation found stored "density" values equal
to population counts, unemployment "rates" of 66-75% and an affordability
"ratio" of 255,734. None of those could have been served if the catalogue's
unit had been checked against the value. These tests pin that check.
"""

from pathlib import Path

import pytest

from soundings.catalogue.contracts import (
    ContractRegistry,
    MeasureContract,
    default_contract_for_unit,
)
from soundings.catalogue.models import IndicatorModel, load_indicators_yaml

REPO = Path(__file__).resolve().parent.parent.parent
INDICATORS_YAML = REPO / "catalogue" / "indicators.yaml"


def _indicator(key: str, unit: str, **contract: object) -> IndicatorModel:
    return IndicatorModel(
        key=key,
        label=key,
        unit=unit,
        source_id="test",
        available_at=["ltla24"],
        contract=MeasureContract(**contract) if contract else None,  # type: ignore[arg-type]
    )


def test_proportion_unit_bounds_values_between_zero_and_one() -> None:
    contract = default_contract_for_unit("proportion")
    assert contract.kind == "proportion"
    assert (contract.minimum, contract.maximum) == (0.0, 1.0)


@pytest.mark.parametrize(
    ("unit", "value"),
    [
        ("proportion", 66.5),  # a percentage stored under a proportion unit
        ("proportion", -0.1),
        ("decile", 0),
        ("decile (1-10)", 11),
        ("rank", 0),
        ("people", -5),
        ("years", 400),
        ("per 1,000 population", -1),
    ],
)
def test_out_of_range_values_violate_the_default_contract(unit: str, value: float) -> None:
    registry = ContractRegistry([_indicator("x.y", unit)])
    assert registry.violation("x.y", value) is not None


@pytest.mark.parametrize(
    ("unit", "value"),
    [
        ("proportion", 0.0),
        ("proportion", 1.0),
        ("proportion", 0.752),
        ("decile", 10),
        ("rank", 32844),
        ("people", 650300),
        ("score", -3.2),
        ("per 1,000 population", 1450.0),  # City of London crime rate is legitimately huge
    ],
)
def test_in_range_values_pass(unit: str, value: float) -> None:
    registry = ContractRegistry([_indicator("x.y", unit)])
    assert registry.violation("x.y", value) is None


def test_explicit_bounds_override_the_unit_default() -> None:
    registry = ContractRegistry(
        [_indicator("housing.affordability_ratio", "ratio", kind="ratio", minimum=0, maximum=60)]
    )
    assert registry.violation("housing.affordability_ratio", 7.6) is None
    message = registry.violation("housing.affordability_ratio", 255734)
    assert message is not None
    assert "housing.affordability_ratio" in message
    assert "60" in message


def test_explicit_contract_inherits_missing_bounds_from_the_unit() -> None:
    registry = ContractRegistry(
        [_indicator("a.share", "proportion", kind="proportion", numerator="n", denominator="d")]
    )
    assert registry.violation("a.share", 45.0) is not None


def test_none_and_unknown_indicators_never_violate() -> None:
    registry = ContractRegistry([_indicator("x.y", "proportion")])
    assert registry.violation("x.y", None) is None
    assert registry.violation("not.in.catalogue", 1e12) is None


def test_unknown_unit_has_no_bounds_but_is_reported() -> None:
    contract = default_contract_for_unit("furlongs per fortnight")
    assert contract.kind == "unclassified"
    assert contract.minimum is None and contract.maximum is None


def test_contract_metadata_is_exposed_for_answers() -> None:
    registry = ContractRegistry(
        [
            _indicator(
                "economy.unemployment_rate",
                "proportion",
                kind="proportion",
                numerator="Unemployed people aged 16-64",
                denominator="Economically active people aged 16-64",
                universe="Residents aged 16-64",
            )
        ]
    )
    contract = registry.contract("economy.unemployment_rate")
    assert contract is not None
    assert contract.denominator == "Economically active people aged 16-64"
    assert registry.unit("economy.unemployment_rate") == "proportion"


def test_must_differ_from_names_the_series_it_must_not_duplicate() -> None:
    registry = ContractRegistry(
        [
            _indicator("population.total", "people"),
            _indicator(
                "population.density",
                "people per km²",
                kind="density",
                must_differ_from=["population.total"],
            ),
        ]
    )
    assert registry.identity_checks() == [("population.density", "population.total")]


# --- the real catalogue ------------------------------------------------------


def test_every_catalogue_unit_is_classified() -> None:
    """A new unit must be given a kind, otherwise its values go unchecked."""
    unclassified = sorted(
        {
            ind.unit
            for ind in load_indicators_yaml(INDICATORS_YAML)
            if default_contract_for_unit(ind.unit).kind == "unclassified" and ind.contract is None
        }
    )
    assert unclassified == []


def test_every_share_or_rate_in_the_catalogue_names_its_denominator() -> None:
    """A rate without a stated denominator is how counts get read as shares."""
    registry = ContractRegistry.from_yaml(INDICATORS_YAML)
    missing = sorted(
        ind.key
        for ind in load_indicators_yaml(INDICATORS_YAML)
        if (contract := registry.contract(ind.key)) is not None
        and contract.kind in {"proportion", "rate", "ratio", "density", "per_person"}
        and not contract.denominator
    )
    assert missing == []


def test_population_density_is_withdrawn() -> None:
    """Stored density values equalled population counts; no area data is loaded."""
    keys = {ind.key for ind in load_indicators_yaml(INDICATORS_YAML)}
    assert "population.density" not in keys
