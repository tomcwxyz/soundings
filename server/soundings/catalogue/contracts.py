"""Measure contracts — what kind of number each indicator is.

Every indicator in ``catalogue/indicators.yaml`` has a unit. A contract turns
that unit into something checkable: the kind of measure, the range a value can
take, and (for shares and rates) the numerator, denominator and universe.

Contracts are enforced in two places:

* when values are served (``soundings.orchestration.measure_guard``), so a
  value that breaks its contract is withheld rather than shown; and
* after every tracked import (``soundings.loader.contract_audit``), so a
  loader that writes such values is recorded as failed.

The registry is built from the YAML, not the database, so it is available to
adapters and tools without a query and cannot drift from the file under
review.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from soundings.catalogue.models import (
    IndicatorModel,
    MeasureContract,
    MeasureKind,
    load_indicators_yaml,
)

__all__ = [
    "ContractRegistry",
    "MeasureContract",
    "MeasureKind",
    "default_contract_for_unit",
    "get_contract_registry",
]

DEFAULT_INDICATORS_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "catalogue" / "indicators.yaml"
)


# unit string -> (kind, minimum, maximum). Counts and rates have no upper
# bound: a crime rate per 1,000 residents can legitimately exceed 1,000 where
# few people live.
_UNIT_DEFAULTS: dict[str, tuple[MeasureKind, float | None, float | None]] = {
    "proportion": ("proportion", 0.0, 1.0),
    "decile": ("decile", 1.0, 10.0),
    "decile (1-10)": ("decile", 1.0, 10.0),
    "rank": ("rank", 1.0, None),
    "score": ("score", None, None),
    "count": ("count", 0.0, None),
    "people": ("count", 0.0, None),
    "households": ("count", 0.0, None),
    "companies": ("count", 0.0, None),
    "charities": ("count", 0.0, None),
    "grants": ("count", 0.0, None),
    "GBP": ("currency", 0.0, None),
    "GBP per week": ("currency", 0.0, None),
    "per 1,000 population": ("rate", 0.0, None),
    "per 1,000 households": ("rate", 0.0, None),
    "per 10,000 population": ("rate", 0.0, None),
    "per 100,000": ("rate", 0.0, None),
    "ratio": ("ratio", 0.0, None),
    "years": ("duration", 0.0, 120.0),
    "µg/m³": ("concentration", 0.0, None),
    "m² per person": ("per_person", 0.0, None),
    "people per km²": ("density", 0.0, None),
}


def default_contract_for_unit(unit: str) -> MeasureContract:
    """The contract implied by a unit alone."""
    kind, minimum, maximum = _UNIT_DEFAULTS.get(unit, ("unclassified", None, None))
    return MeasureContract(kind=kind, minimum=minimum, maximum=maximum)


def _effective(indicator: IndicatorModel) -> MeasureContract:
    base = default_contract_for_unit(indicator.unit)
    explicit = indicator.contract
    if explicit is None:
        return base
    return explicit.model_copy(
        update={
            "minimum": explicit.minimum if explicit.minimum is not None else base.minimum,
            "maximum": explicit.maximum if explicit.maximum is not None else base.maximum,
        }
    )


class ContractRegistry:
    """Contracts for every catalogued indicator, keyed by indicator key."""

    def __init__(self, indicators: list[IndicatorModel]) -> None:
        self._units = {ind.key: ind.unit for ind in indicators}
        self._contracts = {ind.key: _effective(ind) for ind in indicators}

    @classmethod
    def from_yaml(cls, path: Path) -> ContractRegistry:
        return cls(load_indicators_yaml(path))

    def contract(self, indicator_key: str) -> MeasureContract | None:
        return self._contracts.get(indicator_key)

    def unit(self, indicator_key: str) -> str | None:
        return self._units.get(indicator_key)

    def keys(self) -> list[str]:
        return list(self._contracts)

    def violation(self, indicator_key: str, value: float | None) -> str | None:
        """Why ``value`` breaks the indicator's contract, or None if it is fine.

        Unknown indicators and missing values never violate: the guard is for
        values we can positively show to be wrong.
        """
        if value is None:
            return None
        contract = self._contracts.get(indicator_key)
        if contract is None:
            return None
        unit = self._units.get(indicator_key, "")
        if contract.minimum is not None and value < contract.minimum:
            return (
                f"{indicator_key}: value {value:g} is below the minimum "
                f"{contract.minimum:g} for unit '{unit}'"
            )
        if contract.maximum is not None and value > contract.maximum:
            return (
                f"{indicator_key}: value {value:g} is above the maximum "
                f"{contract.maximum:g} for unit '{unit}'"
            )
        return None

    def identity_checks(self) -> list[tuple[str, str]]:
        """(indicator, other) pairs whose stored series must not be identical."""
        return [
            (key, other)
            for key, contract in self._contracts.items()
            for other in contract.must_differ_from
        ]


@lru_cache(maxsize=1)
def get_contract_registry() -> ContractRegistry:
    """The registry for the repository's catalogue, loaded once per process."""
    return ContractRegistry.from_yaml(DEFAULT_INDICATORS_PATH)
