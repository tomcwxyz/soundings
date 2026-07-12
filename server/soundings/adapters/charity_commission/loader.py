"""CharityCommissionLoader — writes the monthly CC bulk register into
`data.organisation` + `data.organisation_operates_in`.

The bulk client streams active charities (status='Registered'); we
batch-resolve their postcodes via the postcodes.io bulk endpoint
(`charity_commission.mapping.resolve_postcodes_to_ltlas`), build
organisation rows, and upsert in chunks. Idempotent: re-running
against the same bulk pull updates `retrieved_at` but doesn't
duplicate rows.

Task 7 layers on top of this to write the
`civil_society.active_charities_count` + `_per_10k` indicator
aggregates at the end of each load.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.base import LoaderAdapter, LoaderResult
from soundings.adapters.charity_commission.area_mapping import build_area_name_to_place_id_map
from soundings.adapters.charity_commission.client import CharityCommissionBulkClient
from soundings.adapters.charity_commission.mapping import resolve_postcodes_to_ltlas
from soundings.adapters.postcodes_io.adapter import PostcodesIoAdapter
from soundings.db.models.data import (
    Organisation,
    OrganisationClassification,
    OrganisationOperatesIn,
)

SOURCE_ID = "charity_commission"
ORG_INSERT_CHUNK = 1000
POSTCODES_IO_TTL_HOURS = 720  # 30 days, matches sources.yaml


class CharityCommissionLoader(LoaderAdapter):
    source_id = SOURCE_ID

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        bulk_client: CharityCommissionBulkClient | None = None,
        postcodes_io: PostcodesIoAdapter | None = None,
    ) -> None:
        super().__init__(engine)
        self._bulk_client = bulk_client or CharityCommissionBulkClient()
        self._postcodes_io = postcodes_io or PostcodesIoAdapter(
            engine, ttl=timedelta(hours=POSTCODES_IO_TTL_HOURS)
        )

    async def load(self, run_id: str | None = None) -> LoaderResult:
        # Pass 1: collect all rows + postcodes. ~50MB in memory at 220k
        # rows — acceptable for v1. Future optimisation could two-pass
        # the bulk download to avoid the in-memory list.
        rows: list[dict[str, Any]] = []
        async for charity in self._bulk_client.iter_active_charities():
            rows.append(charity)

        # Pass 2: batch-resolve postcodes (the resolver short-circuits
        # any postcode already cached in geography.postcode, so monthly
        # re-loads against a warm cache hit postcodes.io zero times).
        postcodes = [r["postcode"] for r in rows if r.get("postcode")]
        resolved = await resolve_postcodes_to_ltlas(self._postcodes_io, postcodes)

        # Pass 3: materialise + chunked upsert.
        retrieved_at = datetime.now(tz=UTC)
        org_rows = self._build_org_rows(rows, resolved, retrieved_at)

        await self._upsert_organisations(org_rows)

        # Pass 4: build operates_in from area-of-operation data.
        # The CC publishes a separate bulk file mapping each charity to
        # the local authorities it operates in.  This is richer than
        # registered-address-only: a charity registered in London but
        # operating in Durham counts toward Durham.
        operates_in_rows = await self._build_operates_in_from_area_of_operation(
            retrieved_at,
        )

        # Also add registered-address rows for charities NOT covered by
        # area-of-operation data (some charities have no area_of_operation
        # entry — they should still be mapped via their registered address).
        area_org_ids = {r["organisation_id"] for r in operates_in_rows}
        for row in self._build_operates_in_rows(org_rows):
            if row["organisation_id"] not in area_org_ids:
                operates_in_rows.append(row)

        await self._replace_operates_in(operates_in_rows, retrieved_at)

        # Pass 5: structured classification codes (What/Who/How) from the
        # CC classification bulk file.  This replaces the noisy free-text
        # `charity_activities` field in the `classification` column with
        # proper structured codes for clean GROUP BY aggregation.
        classification_count = await self._load_classifications()

        # Phase 4 Task 7: end-of-load aggregates into data.indicator_value.
        # Period = YYYY-MM (CC publishes monthly); UPSERT so re-runs in the
        # same calendar month overwrite the latest count.
        period = retrieved_at.strftime("%Y-%m")
        aggregate_notes = await self._aggregate_indicators(period, retrieved_at)

        unresolved = sum(1 for r in rows if not resolved.get(r.get("postcode", "")))
        note_pieces: list[str] = []
        if unresolved:
            note_pieces.append(
                f"{unresolved} charities with unresolved postcodes — "
                "registered_address_place_id null"
            )
        if aggregate_notes:
            note_pieces.append(aggregate_notes)
        if classification_count:
            note_pieces.append(f"{classification_count} classification rows loaded")
        notes = "; ".join(note_pieces) if note_pieces else None
        return LoaderResult(rows_written=len(org_rows), notes=notes)

    async def _aggregate_indicators(self, period: str, retrieved_at: datetime) -> str | None:
        """UPSERT `civil_society.active_charities_count` and
        `civil_society.active_charities_per_10k` into data.indicator_value
        for every LTLA that has fresh CC rows.

        Restricts to organisations touched in THIS load
        (retrieved_at >= the load's threshold, minus a 1-second buffer
        for clock skew). Charities that fell out of the register
        between loads don't carry forward into the new aggregate.

        Per_10k joins to the latest `population.total` for each place;
        LTLAs without a population row get the count but not the rate
        — counted in the returned notes string.
        """
        threshold = retrieved_at - timedelta(seconds=1)
        async with self._engine.begin() as conn:
            # Count UPSERT — overwrites any previous monthly count.
            await conn.execute(
                text(
                    "WITH counts AS ("
                    "  SELECT registered_address_place_id AS place_id, "
                    "         COUNT(*) AS cnt "
                    "  FROM data.organisation "
                    "  WHERE source_id = :sid "
                    "    AND registered_address_place_id IS NOT NULL "
                    "    AND retrieved_at >= :ts "
                    "    AND raw->>'status' = 'Registered' "
                    "  GROUP BY registered_address_place_id"
                    ") "
                    "INSERT INTO data.indicator_value "
                    "(place_id, indicator_key, period, value, source_id, "
                    " retrieved_at, caveats) "
                    "SELECT place_id, 'civil_society.active_charities_count', "
                    "       :period, cnt, :sid, :now, '[]'::jsonb "
                    "FROM counts "
                    "ON CONFLICT (place_id, indicator_key, period) "
                    "DO UPDATE SET value = EXCLUDED.value, "
                    "              retrieved_at = EXCLUDED.retrieved_at, "
                    "              source_id = EXCLUDED.source_id"
                ),
                {
                    "sid": self.source_id,
                    "ts": threshold,
                    "period": period,
                    "now": retrieved_at,
                },
            )
            # Per_10k UPSERT — needs population.total INNER JOIN, so any
            # place without population is skipped silently here. The
            # notes line below logs how many.
            await conn.execute(
                text(
                    "WITH counts AS ("
                    "  SELECT registered_address_place_id AS place_id, "
                    "         COUNT(*) AS cnt "
                    "  FROM data.organisation "
                    "  WHERE source_id = :sid "
                    "    AND registered_address_place_id IS NOT NULL "
                    "    AND retrieved_at >= :ts "
                    "    AND raw->>'status' = 'Registered' "
                    "  GROUP BY registered_address_place_id"
                    "), populations AS ("
                    "  SELECT DISTINCT ON (place_id) place_id, value AS pop "
                    "  FROM data.indicator_value "
                    "  WHERE indicator_key = 'population.total' "
                    "    AND value IS NOT NULL "
                    "  ORDER BY place_id, period DESC"
                    ") "
                    "INSERT INTO data.indicator_value "
                    "(place_id, indicator_key, period, value, source_id, "
                    " retrieved_at, caveats) "
                    "SELECT c.place_id, "
                    "       'civil_society.active_charities_per_10k', "
                    "       :period, c.cnt::numeric / p.pop * 10000.0, "
                    "       :sid, :now, '[]'::jsonb "
                    "FROM counts c "
                    "INNER JOIN populations p ON p.place_id = c.place_id "
                    "WHERE p.pop > 0 "
                    "ON CONFLICT (place_id, indicator_key, period) "
                    "DO UPDATE SET value = EXCLUDED.value, "
                    "              retrieved_at = EXCLUDED.retrieved_at, "
                    "              source_id = EXCLUDED.source_id"
                ),
                {
                    "sid": self.source_id,
                    "ts": threshold,
                    "period": period,
                    "now": retrieved_at,
                },
            )
            # Count places we have a charity-count for but no per_10k —
            # those are the missing-population places.
            missing_pop_row = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) AS n FROM ("
                        "  SELECT registered_address_place_id AS place_id "
                        "  FROM data.organisation "
                        "  WHERE source_id = :sid "
                        "    AND registered_address_place_id IS NOT NULL "
                        "    AND retrieved_at >= :ts "
                        "    AND raw->>'status' = 'Registered' "
                        "  GROUP BY registered_address_place_id"
                        ") c "
                        "WHERE NOT EXISTS ("
                        "  SELECT 1 FROM data.indicator_value iv "
                        "  WHERE iv.place_id = c.place_id "
                        "    AND iv.indicator_key = 'population.total'"
                        ")"
                    ),
                    {"sid": self.source_id, "ts": threshold},
                )
            ).first()
        missing_pop = int(missing_pop_row.n) if missing_pop_row else 0
        if missing_pop:
            return f"{missing_pop} LTLAs without population.total — per_10k skipped"
        return None

    @staticmethod
    def _build_org_rows(
        rows: list[dict[str, Any]],
        resolved: dict[str, str | None],
        retrieved_at: datetime,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for charity in rows:
            postcode = charity.get("postcode") or ""
            place_id = resolved.get(postcode)
            org_id = f"charity_commission:{charity['registration_number']}"
            out.append(
                {
                    "id": org_id,
                    "name": charity["name"],
                    "classification": charity.get("classification") or [],
                    "registered_address_place_id": place_id,
                    "source_id": SOURCE_ID,
                    "retrieved_at": retrieved_at,
                    "raw": charity,
                }
            )
        return out

    @staticmethod
    def _build_operates_in_rows(org_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
        return [
            {"organisation_id": row["id"], "place_id": row["registered_address_place_id"]}
            for row in org_rows
            if row["registered_address_place_id"] is not None
        ]

    async def _build_operates_in_from_area_of_operation(
        self,
        retrieved_at: datetime,
    ) -> list[dict[str, str]]:
        """Download the CC area-of-operation bulk file and map each
        charity → LTLA place_id.

        Returns a list of ``{organisation_id, place_id}`` dicts.  Charities
        whose area descriptions can't be mapped (historic county names like
        ``"Devon"``, ``"Kent"`` — not single LTLAs) are skipped.  Those
        charities will still appear via the registered-address fallback.

        Only charities already in ``data.organisation`` (main entries with
        ``linked_charity_number = 0``) are included — the area-of-operation
        file also contains linked subsidiaries which we don't store.
        """
        area_name_to_place_id = await build_area_name_to_place_id_map(self._engine)
        # Collect existing org IDs to filter out linked subsidiaries
        # that appear in the area-of-operation file but aren't in our org table.
        existing_org_ids: set[str] = set()
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text("SELECT id FROM data.organisation WHERE source_id = :sid"),
                {"sid": SOURCE_ID},
            )
            for row in result.fetchall():
                existing_org_ids.add(row.id)

        rows: list[dict[str, str]] = []
        async for entry in self._bulk_client.iter_area_of_operation():
            place_id = area_name_to_place_id.get(entry["area_description"])
            if not place_id:
                continue
            org_id = f"charity_commission:{entry['registration_number']}"
            if org_id not in existing_org_ids:
                continue
            rows.append({"organisation_id": org_id, "place_id": place_id})
        return rows

    async def _replace_operates_in(
        self,
        operates_in_rows: list[dict[str, str]],
        retrieved_at: datetime,
    ) -> None:
        """Delete all existing CC operates_in rows, then insert fresh ones.

        Unlike the old ``_upsert_operates_in`` (which used ON CONFLICT DO
        NOTHING and could accumulate stale rows across loads), this fully
        replaces the mapping so charities that no longer operate in a place
        are removed.
        """
        if not operates_in_rows:
            # Still clear stale rows even if new set is empty.
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "DELETE FROM data.organisation_operates_in oi "
                        "USING data.organisation o "
                        "WHERE oi.organisation_id = o.id "
                        "  AND o.source_id = :sid"
                    ),
                    {"sid": SOURCE_ID},
                )
            return
        async with self._engine.begin() as conn:
            # Delete existing CC operates_in rows
            await conn.execute(
                text(
                    "DELETE FROM data.organisation_operates_in oi "
                    "USING data.organisation o "
                    "WHERE oi.organisation_id = o.id "
                    "  AND o.source_id = :sid"
                ),
                {"sid": SOURCE_ID},
            )
            # Insert fresh rows in chunks
            for chunk in _chunked(operates_in_rows, ORG_INSERT_CHUNK):
                stmt = insert(OrganisationOperatesIn).values(chunk)
                stmt = stmt.on_conflict_do_nothing()
                await conn.execute(stmt)

    async def _upsert_organisations(self, org_rows: list[dict[str, Any]]) -> None:
        if not org_rows:
            return
        async with self._engine.begin() as conn:
            for chunk in _chunked(org_rows, ORG_INSERT_CHUNK):
                stmt = insert(Organisation).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[Organisation.id],
                    set_={
                        "name": stmt.excluded.name,
                        "classification": stmt.excluded.classification,
                        "registered_address_place_id": stmt.excluded.registered_address_place_id,
                        "retrieved_at": stmt.excluded.retrieved_at,
                        "raw": stmt.excluded.raw,
                    },
                )
                await conn.execute(stmt)

    async def _load_classifications(self) -> int:
        """Download the CC classification bulk file and upsert structured
        cause codes into ``data.organisation_classification``.

        Only rows whose ``organisation_id`` already exists in
        ``data.organisation`` are kept — the classification file includes
        linked subsidiaries and removed charities we don't store.

        Uses DELETE + INSERT (replace strategy) so stale classifications
        are cleared on each load, matching the operates_in approach.

        Returns the number of classification rows inserted.
        """
        # Collect existing org IDs once (same pattern as area-of-operation).
        existing_org_ids: set[str] = set()
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text("SELECT id FROM data.organisation WHERE source_id = :sid"),
                {"sid": SOURCE_ID},
            )
            for row in result.fetchall():
                existing_org_ids.add(row.id)

        rows: list[dict[str, str]] = []
        async for entry in self._bulk_client.iter_classifications():
            org_id = f"charity_commission:{entry['registration_number']}"
            if org_id not in existing_org_ids:
                continue
            if not entry["classification_type"] or not entry["classification_code"]:
                continue
            rows.append(
                {
                    "organisation_id": org_id,
                    "classification_type": entry["classification_type"],
                    "classification_code": entry["classification_code"],
                    "classification_label": entry["classification_label"],
                }
            )

        if not rows:
            # Still clear stale rows.
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "DELETE FROM data.organisation_classification oc "
                        "USING data.organisation o "
                        "WHERE oc.organisation_id = o.id "
                        "  AND o.source_id = :sid"
                    ),
                    {"sid": SOURCE_ID},
                )
            return 0

        async with self._engine.begin() as conn:
            # Clear existing CC classification rows
            await conn.execute(
                text(
                    "DELETE FROM data.organisation_classification oc "
                    "USING data.organisation o "
                    "WHERE oc.organisation_id = o.id "
                    "  AND o.source_id = :sid"
                ),
                {"sid": SOURCE_ID},
            )
            # Insert fresh rows in chunks
            for chunk in _chunked(rows, ORG_INSERT_CHUNK):
                stmt = insert(OrganisationClassification).values(chunk)
                stmt = stmt.on_conflict_do_nothing()
                await conn.execute(stmt)

        return len(rows)


def _chunked(seq: list[Any], n: int) -> list[list[Any]]:
    return [seq[i : i + n] for i in range(0, len(seq), n)]
