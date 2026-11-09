"""Ask measurements must reach stderr under the real Uvicorn logging setup."""

import importlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize("level, visible", [("info", True), ("warning", False)])
def test_ask_metrics_logging_under_uvicorn(level: str, visible: bool) -> None:
    script = f"""
import logging
from uvicorn import Config
from soundings.app import configure_ask_logging
Config("soundings.app:app", log_level="info")
configure_ask_logging({level!r})
configure_ask_logging({level!r})
logging.getLogger("soundings.ask.orchestrator").info("ask_model_turn latency_ms=17")
logging.getLogger("soundings.contribute.auth").info("PRIVATE_AUTH_TOKEN")
logging.getLogger("httpx").info("PRIVATE_HTTP_DETAILS")
"""
    result = subprocess.run(  # noqa: S603 - fixed local interpreter and test script
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("ask_model_turn latency_ms=17") == int(visible)
    assert "PRIVATE_AUTH_TOKEN" not in result.stderr
    assert "PRIVATE_HTTP_DETAILS" not in result.stderr


async def test_startup_configures_ask_logging_before_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_module = importlib.import_module("soundings.app")
    configure = MagicMock()
    monkeypatch.setattr(app_module, "configure_ask_logging", configure)
    monkeypatch.setattr(app_module, "get_settings", lambda: SimpleNamespace(log_level="warning"))
    monkeypatch.setattr(
        app_module, "get_engine", MagicMock(side_effect=RuntimeError("stop before database"))
    )
    with pytest.raises(RuntimeError, match="stop before database"):
        async with app_module.lifespan(app_module.app):
            pass
    configure.assert_called_once_with("warning")
