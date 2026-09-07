"""Coverage metadata for the local grant index."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.grants.store import SOURCE_ID


async def get_grant_index_status(engine: AsyncEngine) -> dict[str, Any]:
    stats_sql = text(
        """
        SELECT COUNT(*) AS grants,
               COUNT(DISTINCT funder_id) FILTER (WHERE funder_id IS NOT NULL) AS funders,
               COUNT(DISTINCT recipient_external_id)
                   FILTER (WHERE recipient_external_id IS NOT NULL) AS recipients,
               MIN(awarded_on) AS earliest_award,
               MAX(awarded_on) AS latest_award,
               MAX(retrieved_at) AS last_indexed_at
        FROM data.grant_record
        WHERE source_id = :source_id
        """
    )
    full_run_sql = text(
        """
        SELECT finished_at
        FROM data.loader_run
        WHERE source_id = :source_id
          AND status = 'ok'
          AND notes LIKE '%grant_index_scope=full%'
        ORDER BY finished_at DESC NULLS LAST
        LIMIT 1
        """
    )
    async with engine.connect() as conn:
        row = (await conn.execute(stats_sql, {"source_id": SOURCE_ID})).mappings().one()
        full_run = (await conn.execute(full_run_sql, {"source_id": SOURCE_ID})).first()

    complete = full_run is not None

    def iso(value: Any) -> str | None:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return str(value.isoformat())
        return str(value)

    return {
        "grants": int(row["grants"] or 0),
        "funders": int(row["funders"] or 0),
        "recipients": int(row["recipients"] or 0),
        "earliest_award": iso(row["earliest_award"]),
        "latest_award": iso(row["latest_award"]),
        "last_indexed_at": iso(row["last_indexed_at"]),
        "coverage": "full-grantnav-export" if complete else "partial-write-through",
        "complete": complete,
    }
