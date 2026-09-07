"""Deterministic analytical queries over the local 360Giving corpus.

This layer deliberately contains no LLM calls. It turns indexed grant evidence
into repeatable summaries that can be exposed through HTTP, MCP and Ask.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.grants.store import SOURCE_ID


class GrantAnalytics:
    """Evidence-first aggregate queries over ``data.grant_record``."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def recipient_profile(
        self,
        recipient: str,
        *,
        top_n: int = 10,
        recent_limit: int = 5,
    ) -> dict[str, Any]:
        """Profile one best-matching recipient and expose match ambiguity.

        Exact Soundings/360Giving IDs win over names. Name lookups select the
        strongest matching recipient identity rather than aggregating every
        organisation whose name happens to contain the query. The candidate
        count is returned so callers can caveat genuinely ambiguous names.
        """
        top_n = max(1, min(top_n, 25))
        recent_limit = max(1, min(recent_limit, 25))
        external_id, local_id = self._recipient_ids(recipient)
        lookup_params = {
            "source_id": SOURCE_ID,
            "recipient": recipient,
            "external_id": external_id,
            "local_id": local_id,
            "recipient_like": f"%{recipient}%",
        }
        candidates_sql = text(
            """
            SELECT recipient_external_id, recipient_org_id, recipient_name,
                   COUNT(*) AS grants,
                   COALESCE(SUM(amount) FILTER (WHERE currency = 'GBP'), 0) AS total_gbp
            FROM data.grant_record
            WHERE source_id = :source_id
              AND (
                    (:external_id IS NOT NULL AND recipient_external_id = :external_id)
                 OR (:local_id IS NOT NULL AND recipient_org_id = :local_id)
                 OR recipient_name ILIKE :recipient_like
              )
            GROUP BY recipient_external_id, recipient_org_id, recipient_name
            ORDER BY
                CASE
                    WHEN :external_id IS NOT NULL
                         AND recipient_external_id = :external_id THEN 0
                    WHEN :local_id IS NOT NULL
                         AND recipient_org_id = :local_id THEN 0
                    WHEN lower(recipient_name) = lower(:recipient) THEN 1
                    ELSE 2
                END,
                grants DESC,
                total_gbp DESC
            LIMIT 25
            """
        )
        async with self._engine.connect() as conn:
            candidates = (await conn.execute(candidates_sql, lookup_params)).mappings().all()
        if not candidates:
            return self._empty_recipient_profile(recipient)

        selected = candidates[0]
        selected_external = selected["recipient_external_id"]
        selected_local = selected["recipient_org_id"]
        selected_name = selected["recipient_name"]
        identity_params = {
            "source_id": SOURCE_ID,
            "selected_external": selected_external,
            "selected_local": selected_local,
            "selected_name": selected_name,
            "top_n": top_n,
            "recent_limit": recent_limit,
        }
        identity_predicate = """
            source_id = :source_id
            AND (
                    (:selected_external IS NOT NULL
                     AND recipient_external_id = :selected_external)
                 OR (:selected_external IS NULL AND :selected_local IS NOT NULL
                     AND recipient_org_id = :selected_local)
                 OR (:selected_external IS NULL AND :selected_local IS NULL
                     AND recipient_name = :selected_name)
            )
        """

        summary_sql = text(
            f"""
            SELECT COUNT(*) AS grants,
                   COALESCE(SUM(amount) FILTER (WHERE currency = 'GBP'), 0) AS total_gbp,
                   AVG(amount) FILTER (WHERE currency = 'GBP') AS average_gbp,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY amount)
                       FILTER (WHERE currency = 'GBP') AS median_gbp,
                   MIN(awarded_on) AS earliest_award,
                   MAX(awarded_on) AS latest_award
            FROM data.grant_record
            WHERE {identity_predicate}
            """
        )
        funders_sql = text(
            f"""
            SELECT COALESCE(funder_name, funder_id, 'Unknown') AS name,
                   funder_id AS id,
                   COUNT(*) AS grants,
                   COALESCE(SUM(amount) FILTER (WHERE currency = 'GBP'), 0) AS total_gbp
            FROM data.grant_record
            WHERE {identity_predicate}
            GROUP BY funder_name, funder_id
            ORDER BY total_gbp DESC, grants DESC, name
            LIMIT :top_n
            """
        )
        programmes_sql = text(
            f"""
            SELECT programme,
                   COUNT(*) AS grants,
                   COALESCE(SUM(amount) FILTER (WHERE currency = 'GBP'), 0) AS total_gbp
            FROM data.grant_record
            WHERE {identity_predicate}
              AND programme IS NOT NULL AND programme <> ''
            GROUP BY programme
            ORDER BY total_gbp DESC, grants DESC, programme
            LIMIT :top_n
            """
        )
        years_sql = text(
            f"""
            SELECT EXTRACT(YEAR FROM awarded_on)::int AS year,
                   COUNT(*) AS grants,
                   COALESCE(SUM(amount) FILTER (WHERE currency = 'GBP'), 0) AS total_gbp
            FROM data.grant_record
            WHERE {identity_predicate}
              AND awarded_on IS NOT NULL
            GROUP BY EXTRACT(YEAR FROM awarded_on)::int
            ORDER BY year
            """
        )
        recent_sql = text(
            f"""
            SELECT id, title, funder_id, funder_name, amount, currency,
                   awarded_on, purpose, programme, beneficiary_place_ids
            FROM data.grant_record
            WHERE {identity_predicate}
            ORDER BY awarded_on DESC NULLS LAST, amount DESC NULLS LAST, id
            LIMIT :recent_limit
            """
        )

        async with self._engine.connect() as conn:
            summary = (await conn.execute(summary_sql, identity_params)).mappings().one()
            funders = (await conn.execute(funders_sql, identity_params)).mappings().all()
            programmes = (await conn.execute(programmes_sql, identity_params)).mappings().all()
            years = (await conn.execute(years_sql, identity_params)).mappings().all()
            recent = (await conn.execute(recent_sql, identity_params)).mappings().all()

        return {
            "recipient_id": selected_external or selected_local,
            "recipient_external_id": selected_external,
            "recipient_org_id": selected_local,
            "recipient_name": selected_name,
            "grants": int(summary["grants"] or 0),
            "total_gbp": self._number(summary["total_gbp"]) or 0.0,
            "average_gbp": self._number(summary["average_gbp"]),
            "median_gbp": self._number(summary["median_gbp"]),
            "earliest_award": self._iso(summary["earliest_award"]),
            "latest_award": self._iso(summary["latest_award"]),
            "top_funders": [self._serialise(dict(row)) for row in funders],
            "top_programmes": [self._serialise(dict(row)) for row in programmes],
            "grants_by_year": [self._serialise(dict(row)) for row in years],
            "recent_grants": [self._serialise(dict(row)) for row in recent],
            "match_candidates": len(candidates),
            "alternative_matches": [
                {
                    "recipient_id": row["recipient_external_id"] or row["recipient_org_id"],
                    "recipient_name": row["recipient_name"],
                    "grants": int(row["grants"] or 0),
                    "total_gbp": self._number(row["total_gbp"]) or 0.0,
                }
                for row in candidates[1:5]
            ],
        }

    @staticmethod
    def _recipient_ids(recipient: str) -> tuple[str | None, str | None]:
        if recipient.startswith("charity_commission:"):
            number = recipient.split(":", 1)[1]
            return f"GB-CHC-{number}", recipient
        if recipient.startswith("GB-CHC-"):
            return recipient, "charity_commission:" + recipient.removeprefix("GB-CHC-")
        if recipient.startswith(("GB-", "360G-")) and " " not in recipient:
            return recipient, None
        return None, None

    @staticmethod
    def _empty_recipient_profile(recipient: str) -> dict[str, Any]:
        return {
            "recipient_id": None,
            "recipient_external_id": None,
            "recipient_org_id": None,
            "recipient_name": recipient,
            "grants": 0,
            "total_gbp": 0.0,
            "average_gbp": None,
            "median_gbp": None,
            "earliest_award": None,
            "latest_award": None,
            "top_funders": [],
            "top_programmes": [],
            "grants_by_year": [],
            "recent_grants": [],
            "match_candidates": 0,
            "alternative_matches": [],
        }

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return float(value)
        return float(value)

    @staticmethod
    def _iso(value: Any) -> str | None:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return str(value.isoformat())
        return str(value)

    @classmethod
    def _serialise(cls, row: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, Decimal):
                out[key] = cls._number(value)
            elif hasattr(value, "isoformat"):
                out[key] = cls._iso(value)
            else:
                out[key] = value
        return out
