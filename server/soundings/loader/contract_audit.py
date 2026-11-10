"""Check what an import wrote against the catalogue's measure contracts.

Loaders upsert through their own SQL, so there is no single write path to
guard. Instead every tracked import is audited once it finishes: if any value
it left behind breaks its indicator's contract, the import is recorded as
failed. The values stay in the table for inspection, and the read-side guard
(``soundings.orchestration.measure_guard``) keeps out-of-range ones from being
served.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.catalogue.contracts import ContractRegistry

# A derived series has to match its base in at least this many paired rows
# before it is called identical; a handful of coincidences is not evidence.
IDENTITY_MIN_PAIRS = 20
_VALUE_TABLES = ("data.indicator_value", "data.trend_point")


class MeasureContractError(Exception):
    """An import wrote values that break their indicators' measure contracts."""


async def audit_source(
    engine: AsyncEngine, source_id: str, contracts: ContractRegistry
) -> list[str]:
    """Return one message per contract broken by ``source_id``'s stored values."""
    violations: list[str] = []
    async with engine.connect() as conn:
        keys = [
            r.key
            for r in (
                await conn.execute(
                    text("SELECT key FROM catalogue.indicator WHERE source_id = :sid ORDER BY key"),
                    {"sid": source_id},
                )
            ).all()
        ]
        for key in keys:
            contract = contracts.contract(key)
            if contract is None:
                continue
            unit = contracts.unit(key)
            for bound, comparison, label in (
                (contract.minimum, "<", "below the minimum"),
                (contract.maximum, ">", "above the maximum"),
            ):
                if bound is None:
                    continue
                count = 0
                for table in _VALUE_TABLES:
                    # `table` and `comparison` come from the constants above,
                    # never from input.
                    sql = (
                        f"SELECT COUNT(*) AS n FROM {table} "  # noqa: S608
                        f"WHERE indicator_key = :k AND value {comparison} :bound"
                    )
                    row = (await conn.execute(text(sql), {"k": key, "bound": bound})).first()
                    count += int(row.n) if row else 0
                if count:
                    violations.append(
                        f"{key}: {count} stored value(s) {label} {bound:g} for unit '{unit}'"
                    )

        owned = set(keys)
        for key, other in contracts.identity_checks():
            if key not in owned:
                continue
            row = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) AS pairs, "
                        "COUNT(*) FILTER (WHERE a.value = b.value) AS same "
                        "FROM data.indicator_value a JOIN data.indicator_value b "
                        "ON b.place_id = a.place_id AND b.period = a.period "
                        "WHERE a.indicator_key = :k AND b.indicator_key = :other "
                        "AND a.value IS NOT NULL AND b.value IS NOT NULL"
                    ),
                    {"k": key, "other": other},
                )
            ).first()
            pairs = int(row.pairs) if row else 0
            same = int(row.same) if row else 0
            if pairs >= IDENTITY_MIN_PAIRS and same == pairs:
                violations.append(
                    f"{key}: all {pairs} stored values are identical to {other}; "
                    "the measure has not been derived"
                )
    return violations
