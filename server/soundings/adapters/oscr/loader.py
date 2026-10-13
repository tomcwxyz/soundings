"""Weekly source-specific OSCR register importer (public bulk ZIP).

Current extract is committed atomically. A registered-address postcode is
NOT evidence that a charity operates throughout its council; OSCR's declared
Main Operating Location is retained separately and not used as 'operates in'.
"""

import json
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.base import LoaderAdapter, LoaderResult
from soundings.adapters.oscr.parser import OscrRecord, parse_register_zip
from soundings.db.models.data import Organisation

SOURCE_ID = "oscr"
DOWNLOAD_URL = "https://www.oscr.org.uk/download/charity-register"
MINIMUM_CHARITIES = 20_000
BATCH_SIZE = 500
COUNT_INDICATOR = "civil_society.oscr_registered_charities_count"


def normalise_area(name: str) -> str:
    cleaned = name.casefold().replace("&", "and").replace("-", " ")
    cleaned = cleaned.replace("city of ", "")
    return re.sub(r"[^a-z0-9]", "", cleaned)


def operating_area_lookup(areas: list[tuple[str, str]]) -> dict[str, str]:
    """Only exact/explicit Scottish local-authority name matches are valid."""
    index: dict[str, str] = {}
    ambiguous: set[str] = set()
    for place_id, place_name in areas:
        key = normalise_area(place_name)
        if key in index and index[key] != place_id:
            ambiguous.add(key)
        else:
            index[key] = place_id
    for key in ambiguous:
        index.pop(key, None)
    # OSCR uses "Western Isles"; ONS generally uses "Na h-Eileanan Siar".
    for alternative in ("westernisles", "nah eileanan siar", "naeile anansiar"):
        if "naheileanansiar" in index:
            index[normalise_area(alternative)] = index["naheileanansiar"]
    return index


def build_organisation_rows(
    records: list[OscrRecord],
    postcodes: dict[str, str],
    council_names: dict[str, str],
    retrieved_at: datetime,
) -> list[dict[str, Any]]:
    materialised: list[dict[str, Any]] = []
    for record in records:
        postcode_place = postcodes.get(record.postcode)
        raw = {
            "registration_number": record.number,
            "postcode": record.postcode,
            "status": record.status,
            "date_of_registration": record.registration_date,
            "latest_income": record.latest_income,
            "latest_expenditure": record.latest_expenditure,
            "main_operating_location": record.main_location,
            "main_operating_place_id": council_names.get(normalise_area(record.main_location)),
            "geographical_spread": record.geographical_spread,
            "website": record.website,
            "regulatory_type": record.regulatory_type,
            "in_current_extract": True,
            "address_place_basis": "postcode" if postcode_place else "unresolved",
        }
        materialised.append(
            {
                "id": f"oscr:{record.number}",
                "name": record.name,
                "classification": list(record.purposes),
                "registered_address_place_id": postcode_place,
                "source_id": SOURCE_ID,
                "retrieved_at": retrieved_at,
                "raw": raw,
            }
        )
    return materialised


class OscrLoader(LoaderAdapter):
    source_id = SOURCE_ID

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        client: httpx.AsyncClient | None = None,
        url: str = DOWNLOAD_URL,
        minimum_charities: int = MINIMUM_CHARITIES,
    ) -> None:
        super().__init__(engine)
        self._client = client
        self._url = url
        self._minimum_charities = minimum_charities

    async def load(self, run_id: str | None = None) -> LoaderResult:
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=180.0, follow_redirects=True)
        try:
            response = await client.get(self._url)
            response.raise_for_status()
            return await self.load_from_bytes(response.content)
        finally:
            if own_client:
                await client.aclose()

    async def load_from_bytes(self, archive: bytes) -> LoaderResult:
        records = parse_register_zip(archive)
        if len(records) < self._minimum_charities:
            raise ValueError(
                f"Incomplete OSCR current-register extract: {len(records)} rows "
                f"(minimum {self._minimum_charities})"
            )

        async with self._engine.connect() as conn:
            council_rows = (
                await conn.execute(
                    text(
                        "SELECT id,name FROM geography.place "
                        "WHERE type = 'ltla24' AND code LIKE 'S12%'"
                    )
                )
            ).all()
            postcode_map: dict[str, str] = {}
            all_postcodes = list({r.postcode for r in records if r.postcode})
            for i in range(0, len(all_postcodes), BATCH_SIZE):
                rows = (
                    await conn.execute(
                        text(
                            "SELECT postcode,ltla24 FROM geography.postcode "
                            "WHERE postcode = ANY(:codes) AND ltla24 IS NOT NULL"
                        ),
                        {"codes": all_postcodes[i : i + BATCH_SIZE]},
                    )
                ).all()
                postcode_map.update((r.postcode, r.ltla24) for r in rows)
        postcode_map = {
            postcode: pid
            for postcode, pid in postcode_map.items()
            if pid.startswith("ltla24:S")
        }
        councils = operating_area_lookup([(row.id, row.name) for row in council_rows])
        timestamp = datetime.now(tz=UTC)
        entries = build_organisation_rows(records, postcode_map, councils, timestamp)

        # Bulk extract is a snapshot, so update everything in one transaction.
        # Obsolete OSCR IDs stay for referential integrity, but become stale.
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE data.organisation SET raw = jsonb_set("
                    "COALESCE(raw,'{}'::jsonb),'{in_current_extract}','false'::jsonb) "
                    "WHERE source_id = :sid"
                ),
                {"sid": SOURCE_ID},
            )
            for start in range(0, len(entries), BATCH_SIZE):
                chunk = entries[start : start + BATCH_SIZE]
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

            counts = Counter(
                entry["registered_address_place_id"]
                for entry in entries
                if entry["registered_address_place_id"] is not None
            )
            caveats = json.dumps(
                [
                    "Postcode-matched registered addresses, not places of activity.",
                    "Unmatched postcodes are excluded; totals are incomplete.",
                    "Do not add this count to Charity Commission totals without deduplication.",
                ]
            )
            for place_id, count in counts.items():
                await conn.execute(
                    text(
                        "INSERT INTO data.indicator_value "
                        "(place_id,indicator_key,period,value,source_id,retrieved_at,caveats) "
                        "VALUES (:pid,:ind,:period,:count,:sid,:ts,CAST(:caveats AS jsonb)) "
                        "ON CONFLICT (place_id,indicator_key,period) DO UPDATE SET "
                        "value=EXCLUDED.value,source_id=EXCLUDED.source_id,"
                        "retrieved_at=EXCLUDED.retrieved_at,caveats=EXCLUDED.caveats"
                    ),
                    {
                        "pid": place_id,
                        "ind": COUNT_INDICATOR,
                        "period": timestamp.strftime("%Y-%m"),
                        "count": count,
                        "sid": SOURCE_ID,
                        "ts": timestamp,
                        "caveats": caveats,
                    },
                )

        located = sum(1 for row in entries if row["registered_address_place_id"])
        main_locations = sum(1 for row in entries if row["raw"]["main_operating_place_id"])
        return LoaderResult(
            rows_written=len(entries),
            notes=(
                f"{len(entries)} OSCR records; {located} matched registered postcodes, "
                f"{len(entries)-located} unmatched; {main_locations} main location names "
                "recognised (not asserted as areas served)"
            ),
        )
