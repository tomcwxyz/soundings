"""Record the outcome of a whole import rather than individual written batches."""

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.base import LoaderResult

_log = logging.getLogger(__name__)


async def run_tracked_loader(
    engine: AsyncEngine,
    source_id: str,
    load: Callable[[], Awaitable[LoaderResult]],
) -> LoaderResult:
    run_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO data.loader_run "
                "(id, source_id, started_at, status, rows_written) "
                "VALUES (:id, :sid, :started, 'running', 0)"
            ),
            {"id": run_id, "sid": source_id, "started": datetime.now(tz=UTC)},
        )
    try:
        result = await load()
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE data.loader_run SET status='ok', finished_at=:finished, "
                    "rows_written=:rows, notes=:notes WHERE id=:id"
                ),
                {
                    "id": run_id,
                    "finished": datetime.now(tz=UTC),
                    "rows": result.rows_written,
                    "notes": result.notes,
                },
            )
        return result
    except BaseException as exc:
        # Preserve the import error if recording it also fails. Partial writes
        # must never produce an 'ok' record, including on task cancellation.
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE data.loader_run SET status='failed', finished_at=:finished, "
                        "notes=:notes WHERE id=:id"
                    ),
                    {
                        "id": run_id,
                        "finished": datetime.now(tz=UTC),
                        "notes": f"{exc.__class__.__name__}: {exc}",
                    },
                )
        except Exception:
            _log.exception("Could not record failed import for %s", source_id)
        raise
