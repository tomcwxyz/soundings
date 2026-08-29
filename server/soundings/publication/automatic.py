"""Automatic publication scheduling for the public question corpus."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from soundings.publication.cli import PublishSummary, publish

logger = logging.getLogger(__name__)

# In the source tree this resolves to <repo>/corpus; in the server image it
# resolves to /app/corpus. Docker Compose mounts the durable corpus_data volume
# at that same path for both the API and loader containers.
DEFAULT_CORPUS_DIR = Path(__file__).resolve().parents[3] / "corpus"
PUBLICATION_CRON = "30 4 1 * *"


def previous_month_period(now: datetime) -> str:
    """Return YYYY-MM for the calendar month immediately before now."""
    now = now.astimezone(UTC)
    if now.month == 1:
        return f"{now.year - 1:04d}-12"
    return f"{now.year:04d}-{now.month - 1:02d}"


def next_month_start(period: str) -> datetime:
    """Exclusive UTC timestamp used by the cumulative snapshot query."""
    year_text, month_text = period.split("-", 1)
    year = int(year_text)
    month = int(month_text)
    if month == 12:
        return datetime(year + 1, 1, 1, tzinfo=UTC)
    return datetime(year, month + 1, 1, tzinfo=UTC)


def current_manifest_period(output_dir: Path) -> str | None:
    """Read the latest published period, treating a bad manifest as absent."""
    manifest_path = output_dir / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None

    period = payload.get("period")
    if not isinstance(period, str):
        return None
    try:
        datetime.strptime(period, "%Y-%m")
    except ValueError:
        return None
    return period


async def publish_previous_month_if_due(
    *,
    output_dir: Path = DEFAULT_CORPUS_DIR,
    now: datetime | None = None,
) -> PublishSummary | None:
    """Publish the latest due cumulative snapshot exactly once.

    This is safe to call on every loader startup. If the current manifest is
    already for the previous calendar month (or a later manually-published
    period), no files are rewritten.
    """
    effective_now = now or datetime.now(tz=UTC)
    target_period = previous_month_period(effective_now)
    published_period = current_manifest_period(output_dir)

    # YYYY-MM sorts chronologically, so a manually published newer period must
    # not be replaced by an older startup catch-up.
    if published_period is not None and published_period >= target_period:
        logger.info(
            "corpus publication already current: published=%s target=%s",
            published_period,
            target_period,
        )
        return None

    logger.info(
        "publishing corpus snapshot: previous=%s target=%s output=%s",
        published_period,
        target_period,
        output_dir,
    )
    return await publish(
        period=target_period,
        output_dir=output_dir,
        period_end=next_month_start(target_period),
        create_git_tag=False,
    )
