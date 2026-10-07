from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select, text

from soundings.adapters.base import _cron_to_window_days
from soundings.db.engine import get_engine
from soundings.db.models.catalogue import Source
from soundings.grants.grantnav_refresh import GRANT_INDEX_REFRESH_CRON
from soundings.grants.status import get_grant_index_status

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, Any]:
    checks: dict[str, str] = {}

    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"fail: {exc.__class__.__name__}"

    try:
        engine = get_engine()
        async with engine.connect() as conn:
            n_sources = (await conn.execute(select(func.count(Source.id)))).scalar_one()
        checks["catalogue"] = "ok" if n_sources > 0 else "empty"
    except Exception as exc:
        checks["catalogue"] = f"fail: {exc.__class__.__name__}"

    try:
        engine = get_engine()
        stale = await _stale_loader_sources(engine)
        checks["loader_runs"] = "ok" if not stale else f"stale: {','.join(stale)}"
    except Exception as exc:
        checks["loader_runs"] = f"fail: {exc.__class__.__name__}"

    try:
        engine = get_engine()
        checks["grant_index"] = await _grant_index_check(engine)
    except Exception as exc:
        checks["grant_index"] = f"fail: {exc.__class__.__name__}"

    try:
        engine = get_engine()
        checks["capture"] = await _capture_check(engine)
    except Exception as exc:
        checks["capture"] = f"fail: {exc.__class__.__name__}"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": overall, "checks": checks}


async def _grant_index_check(engine: object) -> str:
    """Return health for the internal full GrantNav materialisation job.

    The grant index is deliberately not a catalogue.source loader because
    threesixtygiving remains a passthrough source for targeted hydration.
    That means the generic loader freshness check cannot see this job.
    """
    status = await get_grant_index_status(engine)  # type: ignore[arg-type]
    if not status["complete"]:
        return (
            f"incomplete: {status['coverage']}; "
            f"{status['grants']} indexed grant records"
        )

    snapshot = status.get("snapshot")
    if not isinstance(snapshot, dict) or not snapshot.get("finished_at"):
        return "incomplete: full index has no successful snapshot timestamp"

    finished_at = datetime.fromisoformat(str(snapshot["finished_at"]))
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=UTC)
    window_days = _cron_to_window_days(GRANT_INDEX_REFRESH_CRON)
    threshold = timedelta(days=window_days * 1.5)
    if datetime.now(tz=UTC) - finished_at > threshold:
        return f"stale: last full GrantNav snapshot {finished_at.isoformat()}"
    return "ok"

async def _capture_check(engine: object) -> str:
    """Returns 'ok' or a degraded reason.

    Degraded when:
    - the pending-sanitisation backlog exceeds 1000 rows, OR
    - more than 100 records have been pending for over an hour
      (suggests the sanitiser is stuck rather than just busy).
    """
    async with engine.connect() as conn:  # type: ignore[attr-defined]
        backlog = (
            await conn.execute(
                text("SELECT COUNT(*) FROM corpus.question_record WHERE review_status = 'pending'")
            )
        ).scalar_one()
        cutoff = datetime.now(tz=UTC) - timedelta(hours=1)
        stuck = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM corpus.question_record "
                    "WHERE review_status = 'pending' AND timestamp < :cutoff"
                ),
                {"cutoff": cutoff},
            )
        ).scalar_one()
    if backlog > 1000:
        return f"backlog: {backlog} pending"
    if stuck > 100:
        return f"stuck: {stuck} older than 1h"
    return "ok"


async def _stale_loader_sources(engine: object) -> list[str]:
    """Return source_ids whose last successful loader_run is older than
    1.5× refresh_cadence. Sources that have never run successfully are
    listed too.
    """
    async with engine.connect() as conn:  # type: ignore[attr-defined]
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT s.id, s.refresh_cadence,
                           (
                               SELECT MAX(finished_at) FROM data.loader_run
                               WHERE source_id = s.id AND status = 'ok'
                           ) AS last_ok
                    FROM catalogue.source s
                    WHERE s.mode = 'loader'
                    """
                )
            )
        ).all()

    now = datetime.now(tz=UTC)
    stale: list[str] = []
    for row in rows:
        window_days = _cron_to_window_days(row.refresh_cadence)
        threshold = timedelta(days=int(window_days * 1.5))
        if row.last_ok is None:
            stale.append(row.id)
            continue
        if now - row.last_ok > threshold:
            stale.append(row.id)
    return stale
