from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.base import _cron_to_window_days
from soundings.catalogue.models import load_sources_yaml
from soundings.db.engine import get_engine
from soundings.db.models.catalogue import Source
from soundings.grants.grantnav_refresh import GRANT_INDEX_REFRESH_CRON
from soundings.grants.status import get_grant_index_status
from soundings.loader.run import build_source_registry

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
        issues = await _loader_freshness_issues(engine)
        checks["loader_runs"] = (
            "; ".join(f"{kind}: {','.join(sources)}" for kind, sources in issues.items() if sources)
            or "ok"
        )
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
        return f"incomplete: {status['coverage']}; {status['grants']} indexed grant records"

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


async def _loader_freshness_issues(engine: AsyncEngine) -> dict[str, list[str]]:
    """Distinguish overdue imports, failed jobs and missing history honestly.

    Database-only test fixtures and internal manual sign-ups are not scheduled
    datasets. Missing history cannot establish a complete import, even when
    individual materialised rows have recent retrieval timestamps.
    """
    sources_path = Path(__file__).resolve().parents[3] / "catalogue" / "sources.yaml"
    configured = {
        source.id
        for source in load_sources_yaml(sources_path)
        if source.mode == "loader"
        and source.refresh_cadence
        # The full grant job has its own health check and import provenance.
        and source.id != "threesixtygiving"
    }
    registered = build_source_registry(engine).keys()
    issues: dict[str, list[str]] = {
        "failed": [],
        "stale": [],
        "untracked": [],
        "unavailable": sorted(configured - registered),
    }
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT s.id, s.refresh_cadence, "
                    "(SELECT MAX(finished_at) FROM data.loader_run "
                    " WHERE source_id=s.id AND status='ok') AS last_ok, "
                    "attempt.status AS latest_status, attempt.started_at AS latest_started "
                    "FROM catalogue.source s "
                    "LEFT JOIN LATERAL (SELECT status, started_at FROM data.loader_run "
                    " WHERE source_id=s.id ORDER BY started_at DESC LIMIT 1) attempt ON TRUE "
                    "WHERE s.id = ANY(:ids) ORDER BY s.id"
                ),
                {"ids": sorted(configured & registered)},
            )
        ).all()
    now = datetime.now(tz=UTC)
    for row in rows:
        if row.latest_status == "failed" and (
            row.last_ok is None or row.latest_started > row.last_ok
        ):
            issues["failed"].append(row.id)
        elif row.last_ok is None:
            issues["untracked"].append(row.id)
        elif now - row.last_ok > timedelta(days=_cron_to_window_days(row.refresh_cadence) * 1.5):
            issues["stale"].append(row.id)
    return issues
