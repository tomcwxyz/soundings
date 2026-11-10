"""Withhold values that break their measure contract.

The guard sits between adapters and everything that serves a number. A value
outside the range its catalogue unit allows (a "proportion" of 66.5, a
"ratio" of 255,734) is replaced by nothing plus a caveat, so the tools, the
model and the reader all see a gap instead of a wrong figure.

The caveat never repeats the offending number: quoting it would hand the
model exactly the value we are keeping out of the answer.
"""

from __future__ import annotations

import logging

from soundings.catalogue.contracts import ContractRegistry

_log = logging.getLogger(__name__)

# Stable machine-readable prefix, in the style of
# INDICATOR_NOT_AVAILABLE_AT_LEVEL, so prompts and clients can match on it.
WITHHELD_CODE = "VALUE_WITHHELD_FAILED_MEASURE_CONTRACT"


def withheld_caveat(indicator_key: str, unit: str | None, *, count: int = 1) -> str:
    what = "a value" if count == 1 else f"{count} values"
    return (
        f"{WITHHELD_CODE}: {indicator_key}: {what} from the source did not fit the "
        f"indicator's unit ('{unit or 'unknown'}') and was withheld. Treat this indicator "
        "as unavailable here; do not estimate it."
    )


def guard_value(
    contracts: ContractRegistry, indicator_key: str, value: float | None
) -> tuple[float | None, str | None]:
    """Return ``(value, None)`` if it is servable, else ``(None, caveat)``."""
    reason = contracts.violation(indicator_key, value)
    if reason is None:
        return value, None
    _log.warning("measure contract violation withheld: %s", reason)
    return None, withheld_caveat(indicator_key, contracts.unit(indicator_key))


def guard_values(
    contracts: ContractRegistry,
    indicator_key: str,
    values: dict[str, float | None],
) -> tuple[dict[str, float | None], str | None]:
    """Guard a ``{place_id: value}`` map; one summarising caveat for the lot."""
    cleaned: dict[str, float | None] = {}
    withheld = 0
    for place_id, value in values.items():
        if contracts.violation(indicator_key, value) is None:
            cleaned[place_id] = value
        else:
            cleaned[place_id] = None
            withheld += 1
    if not withheld:
        return cleaned, None
    _log.warning(
        "measure contract violations withheld: %s (%d of %d places)",
        indicator_key,
        withheld,
        len(values),
    )
    return cleaned, withheld_caveat(indicator_key, contracts.unit(indicator_key), count=withheld)


def value_bounds(
    contracts: ContractRegistry, indicator_key: str
) -> tuple[float | None, float | None]:
    """(minimum, maximum) for SQL that reads values straight from the table.

    Pass as ``:lo`` / ``:hi`` (NULL = unbounded) so peer sets, percentiles and map colours are
    computed only from servable values.
    """
    contract = contracts.contract(indicator_key)
    if contract is None:
        return None, None
    return contract.minimum, contract.maximum
