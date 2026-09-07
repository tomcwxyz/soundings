"""Import a GrantNav CSV export into Soundings' local grant index.

Usage inside the server environment::

    python -m soundings.grants.import_grantnav_csv /data/grantnav.csv --full-corpus

GrantNav enriches its standard download with recipient/beneficiary geography.
This importer resolves those codes onto Soundings' current LTLA spine in bounded
batches, including ONS code-change mappings where an older district code has
been superseded.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import uuid
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.db.engine import get_engine
from soundings.grants.store import SOURCE_ID, GrantStore

BATCH_SIZE = 2000
MAX_CODE_CHANGE_DEPTH = 5


def _first(row: Mapping[str, str | None], *names: str) -> str | None:
    for name in names:
        value = row.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _place_codes(row: Mapping[str, str | None]) -> list[str]:
    """Collect GrantNav geography codes that may resolve to a current LTLA.

    GrantNav's enriched district fields are preferred, but standard beneficiary
    and recipient location fields are included too. Resolution later filters to
    Soundings' ``ltla24`` places, so region/ward codes are harmless here.
    """
    preferred_names = (
        "Beneficiary District Geographic code (additional data)",
        "Best Available District Geographic Code (additional data)",
        "Recipient District Geographic code (additional data)",
    )
    candidates: list[str] = []
    for name in preferred_names:
        value = _first(row, name)
        if value:
            candidates.append(value)

    for index in range(8):
        value = _first(row, f"Beneficiary Location:{index}:Geographic Code")
        if value:
            candidates.append(value)
    for index in range(3):
        value = _first(row, f"Recipient Org:Location:{index}:Geographic Code")
        if value:
            candidates.append(value)

    return list(dict.fromkeys(candidates))


def _to_api_shape(row: Mapping[str, str | None]) -> dict[str, Any] | None:
    """Translate GrantNav's flat CSV headings to the official API shape.

    The complete source row is retained in ``grantnav_row``. This is
    intentionally broader than the normalised query columns: publisher,
    licence and other GrantNav fields remain available for attribution,
    corrections and later re-materialisation without widening the core table.
    """
    grant_id = _first(row, "Identifier", "Grant Identifier", "id", "identifier")
    if grant_id is None:
        return None

    funder_id = _first(
        row,
        "Funding Org:Identifier",
        "Funding Organisation:Identifier",
        "Funding Organization:Identifier",
        "funder_id",
    )
    funder_name = _first(
        row,
        "Funding Org:Name",
        "Funding Organisation:Name",
        "Funding Organization:Name",
        "funder_name",
    )
    recipient_id = _first(
        row,
        "Recipient Org:Identifier",
        "Recipient Organisation:Identifier",
        "Recipient Organization:Identifier",
        "recipient_id",
    )
    recipient_name = _first(
        row,
        "Recipient Org:Name",
        "Recipient Organisation:Name",
        "Recipient Organization:Name",
        "recipient_name",
    )

    data: dict[str, Any] = {
        "id": grant_id,
        "title": _first(row, "Title", "title"),
        "description": _first(row, "Description", "description"),
        "currency": _first(row, "Currency", "currency"),
        "amountAwarded": _first(
            row,
            "Amount Awarded",
            "amountAwarded",
            "amount_awarded",
        ),
        "awardDate": _first(row, "Award Date", "awardDate", "award_date"),
        "grantProgramme": _first(
            row,
            "Grant Programme:Title",
            "Grant Programme",
            "programme",
        ),
        "fundingOrganization": [{"id": funder_id, "name": funder_name}],
        "recipientOrganization": [{"id": recipient_id, "name": recipient_name}],
    }
    return {
        "data": data,
        "grantnav_row": dict(row),
        "soundings_place_codes": _place_codes(row),
    }


async def _resolve_place_codes(
    engine: AsyncEngine,
    codes: set[str],
) -> dict[str, list[str]]:
    """Resolve raw ONS codes to current Soundings LTLA place IDs.

    Direct current codes are matched first. A bounded recursive CTE follows the
    existing ``geography.code_change`` spine for older/reorganised codes; splits
    can therefore resolve one historic code to more than one current place.
    """
    if not codes:
        return {}

    direct_stmt = text(
        "SELECT code, id FROM geography.place WHERE type = 'ltla24' AND code IN :codes"
    ).bindparams(bindparam("codes", expanding=True))
    changed_stmt = text(
        """
        WITH RECURSIVE changes(source_code, current_code, depth) AS (
            SELECT old_code, new_code, 1
            FROM geography.code_change
            WHERE old_code IN :codes
          UNION ALL
            SELECT changes.source_code, cc.new_code, changes.depth + 1
            FROM changes
            JOIN geography.code_change cc ON cc.old_code = changes.current_code
            WHERE changes.depth < :max_depth
        )
        SELECT changes.source_code, p.id
        FROM changes
        JOIN geography.place p
          ON p.code = changes.current_code AND p.type = 'ltla24'
        """
    ).bindparams(bindparam("codes", expanding=True))

    resolved: defaultdict[str, set[str]] = defaultdict(set)
    params = {"codes": sorted(codes), "max_depth": MAX_CODE_CHANGE_DEPTH}
    async with engine.connect() as conn:
        direct = await conn.execute(direct_stmt, {"codes": params["codes"]})
        for row in direct:
            resolved[str(row.code)].add(str(row.id))
        changed = await conn.execute(changed_stmt, params)
        for row in changed:
            resolved[str(row.source_code)].add(str(row.id))

    return {code: sorted(place_ids) for code, place_ids in resolved.items()}


async def _attach_soundings_places(
    engine: AsyncEngine,
    batch: list[dict[str, Any]],
) -> None:
    codes = {str(code) for raw in batch for code in raw.get("soundings_place_codes", []) if code}
    resolved = await _resolve_place_codes(engine, codes)
    for raw in batch:
        place_ids = {
            place_id
            for code in raw.get("soundings_place_codes", [])
            for place_id in resolved.get(str(code), [])
        }
        raw["soundings_beneficiary_place_ids"] = sorted(place_ids)


def _run_provenance(
    path: Path,
    *,
    full_corpus: bool,
    provenance: Mapping[str, Any] | None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "dataset": "GrantNav",
        "aggregator": "360Giving",
        "format": "csv",
        "coverage": "full" if full_corpus else "partial",
        "acquisition": "local-file",
        "file_name": path.name,
        "bytes": path.stat().st_size,
        "grantnav_attribution": "Contains data from GrantNav, a 360Giving application",
        "grantnav_licence": "CC-BY-SA",
        "original_publisher_metadata": "preserved in grant_record.raw.grantnav_row",
    }
    if provenance:
        base.update(dict(provenance))
    return base


async def _start_run(
    engine: AsyncEngine,
    *,
    provenance: Mapping[str, Any],
) -> tuple[uuid.UUID, datetime]:
    run_id = uuid.uuid4()
    started = datetime.now(tz=UTC)
    stmt = text(
        "INSERT INTO data.loader_run "
        "(id, source_id, started_at, status, rows_written, notes, provenance) "
        "VALUES (:id, :sid, :started, 'running', 0, 'grant_index_import', :provenance)"
    ).bindparams(bindparam("provenance", type_=JSONB))
    async with engine.begin() as conn:
        await conn.execute(
            stmt,
            {
                "id": run_id,
                "sid": SOURCE_ID,
                "started": started,
                "provenance": dict(provenance),
            },
        )
    return run_id, started


async def _finish_run(
    engine: AsyncEngine,
    run_id: uuid.UUID,
    *,
    rows_written: int,
    full_corpus: bool,
) -> None:
    notes = "grant_index_scope=full" if full_corpus else "grant_index_scope=partial"
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE data.loader_run SET status='ok', finished_at=:finished, "
                "rows_written=:rows, notes=:notes WHERE id=:id"
            ),
            {
                "finished": datetime.now(tz=UTC),
                "rows": rows_written,
                "notes": notes,
                "id": run_id,
            },
        )


async def _fail_run(engine: AsyncEngine, run_id: uuid.UUID, exc: Exception) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE data.loader_run SET status='failed', finished_at=:finished, "
                "notes=:notes WHERE id=:id"
            ),
            {
                "finished": datetime.now(tz=UTC),
                "notes": f"{exc.__class__.__name__}: {exc}",
                "id": run_id,
            },
        )


async def import_grantnav_csv(
    engine: AsyncEngine,
    path: Path,
    *,
    full_corpus: bool = False,
    provenance: Mapping[str, Any] | None = None,
) -> int:
    """Stream a GrantNav CSV into the local index in bounded batches.

    ``provenance`` describes the acquired snapshot (URL, digest, HTTP
    validators, etc.). It is stored once on the loader run; each grant retains
    its original GrantNav row for publisher-level attribution and corrections.
    """
    store = GrantStore(engine)
    run_provenance = _run_provenance(
        path,
        full_corpus=full_corpus,
        provenance=provenance,
    )
    run_id, started = await _start_run(engine, provenance=run_provenance)
    written = 0
    batch: list[dict[str, Any]] = []

    async def flush() -> None:
        nonlocal written
        if not batch:
            return
        await _attach_soundings_places(engine, batch)
        written += await store.upsert_api_grants(batch)
        batch.clear()

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                raw = _to_api_shape(row)
                if raw is None:
                    continue
                batch.append(raw)
                if len(batch) >= BATCH_SIZE:
                    await flush()
            await flush()

        if full_corpus:
            # Only remove records absent from the new export AFTER a successful
            # import. Interrupted imports therefore leave the previous index usable.
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "DELETE FROM data.grant_record "
                        "WHERE source_id=:sid AND retrieved_at < :started"
                    ),
                    {"sid": SOURCE_ID, "started": started},
                )
        await _finish_run(
            engine,
            run_id,
            rows_written=written,
            full_corpus=full_corpus,
        )
        return written
    except Exception as exc:
        await _fail_run(engine, run_id, exc)
        raise


async def _main(path: Path, *, full_corpus: bool) -> int:
    if not path.is_file():
        print(f"GrantNav CSV not found: {path}", file=sys.stderr)
        return 2
    written = await import_grantnav_csv(get_engine(), path, full_corpus=full_corpus)
    print(f"[360giving] indexed {written} grants from {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="soundings-360giving-import")
    parser.add_argument("path", type=Path, help="Path to a GrantNav CSV export")
    parser.add_argument(
        "--full-corpus",
        action="store_true",
        help="Mark as a complete export and remove older grants absent from this import",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_main(args.path, full_corpus=args.full_corpus))


if __name__ == "__main__":
    sys.exit(main())
