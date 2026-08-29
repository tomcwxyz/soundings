"""Tests for automatic corpus publication scheduling."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from soundings.publication import automatic
from soundings.publication.automatic import (
    current_manifest_period,
    next_month_start,
    previous_month_period,
    publish_previous_month_if_due,
)


def test_previous_month_period_crosses_year_boundary() -> None:
    assert previous_month_period(datetime(2026, 8, 29, tzinfo=UTC)) == "2026-07"
    assert previous_month_period(datetime(2026, 1, 2, tzinfo=UTC)) == "2025-12"


def test_next_month_start_crosses_year_boundary() -> None:
    assert next_month_start("2026-07") == datetime(2026, 8, 1, tzinfo=UTC)
    assert next_month_start("2026-12") == datetime(2027, 1, 1, tzinfo=UTC)


def test_current_manifest_period_tolerates_missing_or_invalid_manifest(tmp_path: Path) -> None:
    assert current_manifest_period(tmp_path) is None

    (tmp_path / "manifest.json").write_text("{not json")
    assert current_manifest_period(tmp_path) is None

    (tmp_path / "manifest.json").write_text(json.dumps({"period": "July"}))
    assert current_manifest_period(tmp_path) is None

    (tmp_path / "manifest.json").write_text(json.dumps({"period": "2026-07"}))
    assert current_manifest_period(tmp_path) == "2026-07"


@pytest.mark.asyncio
async def test_publish_previous_month_skips_when_manifest_is_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"period": "2026-07"}))

    async def fail_publish(**kwargs: Any) -> Any:
        del kwargs
        raise AssertionError("publish should not be called")

    monkeypatch.setattr(automatic, "publish", fail_publish)

    result = await publish_previous_month_if_due(
        output_dir=tmp_path,
        now=datetime(2026, 8, 29, tzinfo=UTC),
    )

    assert result is None


@pytest.mark.asyncio
async def test_publish_previous_month_catches_up_after_missed_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"period": "2026-04"}))
    captured: dict[str, Any] = {}

    async def fake_publish(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "published"

    monkeypatch.setattr(automatic, "publish", fake_publish)

    result = await publish_previous_month_if_due(
        output_dir=tmp_path,
        now=datetime(2026, 8, 29, tzinfo=UTC),
    )

    assert result == "published"
    assert captured == {
        "period": "2026-07",
        "output_dir": tmp_path,
        "period_end": datetime(2026, 8, 1, tzinfo=UTC),
        "create_git_tag": False,
    }


@pytest.mark.asyncio
async def test_newer_manual_publication_is_not_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"period": "2026-08"}))

    async def fail_publish(**kwargs: Any) -> Any:
        del kwargs
        raise AssertionError("publish should not be called")

    monkeypatch.setattr(automatic, "publish", fail_publish)

    result = await publish_previous_month_if_due(
        output_dir=tmp_path,
        now=datetime(2026, 8, 29, tzinfo=UTC),
    )

    assert result is None
