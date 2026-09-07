"""Import a GrantNav CSV export into Soundings' local grant index.

Usage inside the server environment::

    python -m soundings.grants.import_grantnav_csv /data/grantnav.csv --full-corpus

The importer intentionally accepts a local CSV rather than scraping GrantNav.
360Giving documents GrantNav full-dataset downloads and Datastore access as the
supported bulk routes. A later loader can automate retrieval without changing
the ``GrantStore`` or tool contracts introduced here.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.db.engine import get_engine
from soundings.grants.store import SOURCE_ID, GrantStore

BATCH_SIZE = 2000


def _first(row: Mapping[str, str | None], *names: str) -> str | None:
    for name in names:
        value = row.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _to_api_shape(row: Mapping[str, str | None]) -> dict[str, Any] | None:
    """Translate GrantNav's flat CSV headings to the official API shape."""
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
    return {"data": data, "grantnav_row": dict(row)}


async def _start_run(engine: AsyncEngine) -> tuple[uuid.UUID, datetime]:
    run_id = uuid.uuid4()
    started = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO data.loader_run "
                "(id, source_id, started_at, status, rows_written, notes) "
                "VALUES (:id, :sid, :started, 'running', 0, 'grant_index_import')"
            ),
            {"id": run_id, "sid": SOURCE_ID, "started": started},
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
) -> int:
    """Stream a GrantNav CSV into the local index in bounded batches."""
    store = GrantStore(engine)
    run_id, started = await _start_run(engine)
    written = 0
    batch: list[dict[str, Any]] = []

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                raw = _to_api_shape(row)
                if raw is None:
                    continue
                batch.append(raw)
                if len(batch) >= BATCH_SIZE:
                    written += await store.upsert_api_grants(batch)
                    batch.clear()
            if batch:
                written += await store.upsert_api_grants(batch)

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
