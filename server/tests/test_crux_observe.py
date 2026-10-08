"""Tests for the opt-in Soundings CRUX observation payload."""

import json

from soundings.ask.crux_observe import build_crux_ingest_batch


def test_crux_ask_batch_is_metadata_only() -> None:
    batch = build_crux_ingest_batch(
        workflow="ask",
        provider="anthropic",
        operation="messages.create",
        system_version_ref="system-version:soundings-ask:0.1",
        producer_id="producer:soundings",
        request_model="claude-sonnet-5",
        response_model="claude-sonnet-5-20260901",
        finish_reason="tool_use",
        input_tokens=123,
        output_tokens=45,
        occurred_at="2026-09-19T20:45:00+00:00",
        run_id="run:soundings:test-1",
    )

    assert batch["system_version_ref"] == "system-version:soundings-ask:0.1"
    assert batch["runs"][0]["capture_mode"] == "metadata_only"
    assert batch["events"][0]["attributes"] == {
        "workflow": "ask",
        "provider": "anthropic",
        "operation": "messages.create",
        "request_model": "claude-sonnet-5",
        "response_model": "claude-sonnet-5-20260901",
        "finish_reason": "tool_use",
        "input_tokens": 123,
        "output_tokens": 45,
    }

    serialised = json.dumps(batch).lower()
    for forbidden in (
        "prompt",
        "question",
        "thinking",
        "tool_input",
        "tool_result",
        "answer",
        "source_data",
    ):
        assert forbidden not in serialised
