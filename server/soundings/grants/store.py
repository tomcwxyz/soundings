"""Local query/index layer for 360Giving grants.

The live 360Giving API is organisation-centric and rate-limited. This store
makes grant records queryable by topic, funder, recipient, date and place once
they have been indexed. Existing targeted API calls write through to the store,
so useful local coverage grows immediately; a bulk GrantNav/Datastore loader can
populate the same table later without changing tool contracts.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.db.models.data import GrantRecord

SOURCE_ID = "threesixtygiving"


class GrantStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def upsert_api_grants(self, raw_grants: list[dict[str, Any]]) -> int:
        """Materialise 360Giving API grant payloads into ``data.grant_record``.

        The method is intentionally idempotent and tolerant of partially-filled
        records. It keeps the original payload in ``raw`` for provenance and
        later re-materialisation as our index evolves.
        """
        if not raw_grants:
            return 0

        retrieved_at = datetime.now(tz=UTC)
        rows = [self._normalise_api_grant(raw, retrieved_at) for raw in raw_grants]
        rows = [row for row in rows if row is not None]
        if not rows:
            return 0

        # Link 360Giving charity IDs to Soundings' existing Charity Commission
        # organisation rows where possible. Keep recipient_external_id regardless
        # so non-CC organisations remain searchable.
        candidates = {
            row["recipient_external_id"]: self._to_local_org_id(row["recipient_external_id"])
            for row in rows
            if row.get("recipient_external_id")
        }
        local_ids = [value for value in candidates.values() if value]
        existing: set[str] = set()
        if local_ids:
            stmt = text("SELECT id FROM data.organisation WHERE id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            )
            async with self._engine.connect() as conn:
                result = await conn.execute(stmt, {"ids": local_ids})
                existing = {str(row.id) for row in result}

        for row in rows:
            external_id = row.get("recipient_external_id")
            local_id = candidates.get(external_id) if external_id else None
            row["recipient_org_id"] = local_id if local_id in existing else None

        async with self._engine.begin() as conn:
            for start in range(0, len(rows), 1000):
                chunk = rows[start : start + 1000]
                stmt = insert(GrantRecord).values(chunk)
                excluded = stmt.excluded
                stmt = stmt.on_conflict_do_update(
                    index_elements=[GrantRecord.id],
                    set_={
                        "funder_id": excluded.funder_id,
                        "funder_name": excluded.funder_name,
                        "recipient_org_id": excluded.recipient_org_id,
                        "recipient_external_id": excluded.recipient_external_id,
                        "recipient_name": excluded.recipient_name,
                        "title": excluded.title,
                        "amount": excluded.amount,
                        "currency": excluded.currency,
                        "awarded_on": excluded.awarded_on,
                        "purpose": excluded.purpose,
                        "programme": excluded.programme,
                        "beneficiary_place_ids": excluded.beneficiary_place_ids,
                        "source_id": excluded.source_id,
                        "retrieved_at": excluded.retrieved_at,
                        "raw": excluded.raw,
                    },
                )
                await conn.execute(stmt)
        return len(rows)

    async def search(
        self,
        *,
        query: str | None = None,
        funder: str | None = None,
        recipient: str | None = None,
        place_id: str | None = None,
        awarded_from: date | None = None,
        awarded_to: date | None = None,
        amount_min: float | None = None,
        amount_max: float | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        where = ["g.source_id = :source_id"]
        params: dict[str, Any] = {"source_id": SOURCE_ID, "limit": limit, "offset": offset}

        if query:
            where.append("g.search_document @@ websearch_to_tsquery('english', :query)")
            params["query"] = query
        if funder:
            where.append("(g.funder_id = :funder OR g.funder_name ILIKE :funder_like)")
            params["funder"] = funder
            params["funder_like"] = f"%{funder}%"
        if recipient:
            where.append(
                "(g.recipient_external_id = :recipient OR g.recipient_org_id = :recipient "
                "OR g.recipient_name ILIKE :recipient_like)"
            )
            params["recipient"] = recipient
            params["recipient_like"] = f"%{recipient}%"
        if place_id:
            where.append(
                "(g.beneficiary_place_ids @> ARRAY[:place_id]::varchar[] OR EXISTS ("
                "SELECT 1 FROM data.organisation_operates_in oi "
                "WHERE oi.organisation_id = g.recipient_org_id AND oi.place_id = :place_id))"
            )
            params["place_id"] = place_id
        if awarded_from:
            where.append("g.awarded_on >= :awarded_from")
            params["awarded_from"] = awarded_from
        if awarded_to:
            where.append("g.awarded_on <= :awarded_to")
            params["awarded_to"] = awarded_to
        if amount_min is not None:
            where.append("g.amount >= :amount_min")
            params["amount_min"] = amount_min
        if amount_max is not None:
            where.append("g.amount <= :amount_max")
            params["amount_max"] = amount_max

        predicate = " AND ".join(where)
        rank_sql = (
            "ts_rank_cd(g.search_document, websearch_to_tsquery('english', :query))"
            if query
            else "0.0"
        )
        sql = text(
            f"""
            SELECT g.id, g.title, g.funder_id, g.funder_name,
                   g.recipient_external_id, g.recipient_name,
                   g.amount, g.currency, g.awarded_on, g.purpose, g.programme,
                   g.beneficiary_place_ids, {rank_sql} AS relevance
            FROM data.grant_record g
            WHERE {predicate}
            ORDER BY relevance DESC, g.awarded_on DESC NULLS LAST, g.amount DESC NULLS LAST
            LIMIT :limit OFFSET :offset
            """
        )
        count_sql = text(f"SELECT COUNT(*) FROM data.grant_record g WHERE {predicate}")

        async with self._engine.connect() as conn:
            result = (await conn.execute(sql, params)).mappings().all()
            total = int((await conn.execute(count_sql, params)).scalar_one())

        return {
            "grants": [self._serialise_row(dict(row)) for row in result],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def funder_profile(self, funder: str, *, top_n: int = 10) -> dict[str, Any]:
        top_n = max(1, min(top_n, 25))
        params = {"source_id": SOURCE_ID, "funder": funder, "funder_like": f"%{funder}%", "top_n": top_n}
        predicate = (
            "source_id = :source_id AND (funder_id = :funder OR funder_name ILIKE :funder_like)"
        )
        summary_sql = text(
            f"""
            SELECT COUNT(*) AS grants,
                   COALESCE(SUM(amount) FILTER (WHERE currency = 'GBP'), 0) AS total_gbp,
                   AVG(amount) FILTER (WHERE currency = 'GBP') AS average_gbp,
                   MIN(awarded_on) AS earliest_award,
                   MAX(awarded_on) AS latest_award,
                   MIN(funder_id) AS funder_id,
                   MIN(funder_name) AS funder_name
            FROM data.grant_record
            WHERE {predicate}
            """
        )
        recipients_sql = text(
            f"""
            SELECT COALESCE(recipient_name, recipient_external_id, recipient_org_id, 'Unknown') AS name,
                   recipient_external_id AS id,
                   COUNT(*) AS grants,
                   COALESCE(SUM(amount) FILTER (WHERE currency = 'GBP'), 0) AS total_gbp
            FROM data.grant_record
            WHERE {predicate}
            GROUP BY recipient_name, recipient_external_id, recipient_org_id
            ORDER BY total_gbp DESC, grants DESC
            LIMIT :top_n
            """
        )
        programmes_sql = text(
            f"""
            SELECT programme, COUNT(*) AS grants,
                   COALESCE(SUM(amount) FILTER (WHERE currency = 'GBP'), 0) AS total_gbp
            FROM data.grant_record
            WHERE {predicate} AND programme IS NOT NULL AND programme <> ''
            GROUP BY programme
            ORDER BY total_gbp DESC, grants DESC
            LIMIT :top_n
            """
        )
        async with self._engine.connect() as conn:
            summary = (await conn.execute(summary_sql, params)).mappings().one()
            recipients = (await conn.execute(recipients_sql, params)).mappings().all()
            programmes = (await conn.execute(programmes_sql, params)).mappings().all()
        return {
            "funder_id": summary["funder_id"],
            "funder_name": summary["funder_name"],
            "grants": int(summary["grants"] or 0),
            "total_gbp": self._number(summary["total_gbp"]),
            "average_gbp": self._number(summary["average_gbp"]),
            "earliest_award": self._iso(summary["earliest_award"]),
            "latest_award": self._iso(summary["latest_award"]),
            "top_recipients": [self._serialise_row(dict(row)) for row in recipients],
            "top_programmes": [self._serialise_row(dict(row)) for row in programmes],
        }

    async def index_status(self) -> dict[str, Any]:
        sql = text(
            """
            SELECT COUNT(*) AS grants,
                   COUNT(DISTINCT funder_id) FILTER (WHERE funder_id IS NOT NULL) AS funders,
                   COUNT(DISTINCT recipient_external_id) FILTER (WHERE recipient_external_id IS NOT NULL) AS recipients,
                   MIN(awarded_on) AS earliest_award,
                   MAX(awarded_on) AS latest_award,
                   MAX(retrieved_at) AS last_indexed_at
            FROM data.grant_record
            WHERE source_id = :source_id
            """
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(sql, {"source_id": SOURCE_ID})).mappings().one()
        return {
            "grants": int(row["grants"] or 0),
            "funders": int(row["funders"] or 0),
            "recipients": int(row["recipients"] or 0),
            "earliest_award": self._iso(row["earliest_award"]),
            "latest_award": self._iso(row["latest_award"]),
            "last_indexed_at": self._iso(row["last_indexed_at"]),
            # Until the bulk loader lands, coverage consists of records written
            # through by targeted 360Giving API calls. Make that explicit so an
            # agent never mistakes an empty/partial index for the whole corpus.
            "coverage": "partial-write-through",
            "complete": False,
        }

    @staticmethod
    def _normalise_api_grant(raw: dict[str, Any], retrieved_at: datetime) -> dict[str, Any] | None:
        data = raw.get("data") or {}
        grant_id = data.get("id") or raw.get("grant_id")
        if not grant_id:
            return None

        funder = GrantStore._first_org(data.get("fundingOrganization"))
        recipient = GrantStore._first_org(data.get("recipientOrganization"))
        award_date = GrantStore._parse_date(data.get("awardDate"))
        amount = data.get("amountAwarded")

        return {
            "id": str(grant_id),
            "funder_id": GrantStore._str_or_none(funder.get("id")),
            "funder_name": GrantStore._str_or_none(funder.get("name")),
            "recipient_org_id": None,
            "recipient_external_id": GrantStore._str_or_none(recipient.get("id")),
            "recipient_name": GrantStore._str_or_none(recipient.get("name")),
            "title": GrantStore._str_or_none(data.get("title")),
            "amount": float(amount) if amount is not None else None,
            "currency": GrantStore._str_or_none(data.get("currency")),
            "awarded_on": award_date,
            "purpose": GrantStore._str_or_none(data.get("description") or data.get("title")),
            "programme": GrantStore._programme(data.get("grantProgramme")),
            "beneficiary_place_ids": [],
            "source_id": SOURCE_ID,
            "retrieved_at": retrieved_at,
            "raw": raw,
        }

    @staticmethod
    def _first_org(value: Any) -> dict[str, Any]:
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
        if isinstance(value, dict):
            return value
        return {}

    @staticmethod
    def _programme(value: Any) -> str | None:
        if isinstance(value, str):
            return value or None
        if isinstance(value, dict):
            return GrantStore._str_or_none(value.get("title") or value.get("name"))
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str) and item:
                    parts.append(item)
                elif isinstance(item, dict):
                    label = item.get("title") or item.get("name")
                    if label:
                        parts.append(str(label))
            return "; ".join(parts) or None
        return None

    @staticmethod
    def _to_local_org_id(external_id: str) -> str | None:
        if external_id.startswith("GB-CHC-"):
            return "charity_commission:" + external_id.removeprefix("GB-CHC-")
        return None

    @staticmethod
    def _parse_date(value: Any) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None

    @staticmethod
    def _str_or_none(value: Any) -> str | None:
        if value is None:
            return None
        result = str(value).strip()
        return result or None

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (int, float)):
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
    def _serialise_row(cls, row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: cls._number(value)
            if isinstance(value, Decimal)
            else cls._iso(value)
            if isinstance(value, (date, datetime))
            else value
            for key, value in row.items()
        }
