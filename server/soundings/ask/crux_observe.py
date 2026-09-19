"""Optional metadata-only CRUX observation for the Soundings Ask loop."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)


def _read_config() -> tuple[str, str, str, str] | None:
    ingest_url = os.getenv("CRUX_INGEST_URL")
    ingest_token = os.getenv("CRUX_INGEST_TOKEN")
    system_version_ref = os.getenv("CRUX_SYSTEM_VERSION_REF")
    producer_id = os.getenv("CRUX_PRODUCER_ID")
    if (
        ingest_url is None
        or ingest_token is None
        or system_version_ref is None
        or producer_id is None
    ):
        return None
    return ingest_url, ingest_token, system_version_ref, producer_id


def build_crux_ingest_batch(
    *,
    workflow: str,
    provider: str,
    operation: str,
    system_version_ref: str,
    producer_id: str,
    request_model: str | None = None,
    response_model: str | None = None,
    finish_reason: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    occurred_at: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build one metadata-only CRUX Run/Event batch.

    No prompt, answer, reasoning, source data or tool payload is accepted by
    this API. Keep the signature intentionally narrow.
    """
    timestamp = occurred_at or datetime.now(tz=UTC).isoformat()
    resolved_run_id = run_id or f"run:{producer_id}:{uuid4()}"
    event_id = f"event:{resolved_run_id.removeprefix('run:')}:1"

    attributes: dict[str, Any] = {
        "workflow": workflow,
        "provider": provider,
        "operation": operation,
    }
    if request_model:
        attributes["request_model"] = request_model
    if response_model:
        attributes["response_model"] = response_model
    if finish_reason:
        attributes["finish_reason"] = finish_reason
    if input_tokens is not None:
        attributes["input_tokens"] = input_tokens
    if output_tokens is not None:
        attributes["output_tokens"] = output_tokens

    return {
        "format": "crux-ingest/0.1",
        "request_id": f"request:{resolved_run_id.removeprefix('run:')}",
        "producer": {
            "id": producer_id,
            "kind": "application",
            "name": "Soundings",
        },
        "system_version_ref": system_version_ref,
        "runs": [
            {
                "schema_version": "0.1",
                "id": resolved_run_id,
                "system_version_ref": system_version_ref,
                "started_at": timestamp,
                "completed_at": timestamp,
                "status": "completed",
                "capture_mode": "metadata_only",
                "disclosure": "internal",
                "external_refs": [],
            }
        ],
        "events": [
            {
                "schema_version": "0.1",
                "id": event_id,
                "run_ref": resolved_run_id,
                "sequence": 1,
                "occurred_at": timestamp,
                "type": "ai_invocation",
                "attributes": attributes,
                "disclosure": "internal",
            }
        ],
        "observations": [],
        "evidence_envelopes": [],
    }


async def emit_crux_ai_invocation(
    *,
    workflow: str,
    provider: str,
    operation: str,
    request_model: str | None = None,
    response_model: str | None = None,
    finish_reason: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    """Best-effort metadata delivery; disabled unless all four env vars exist."""
    config = _read_config()
    if config is None:
        return

    ingest_url, ingest_token, system_version_ref, producer_id = config
    body = build_crux_ingest_batch(
        workflow=workflow,
        provider=provider,
        operation=operation,
        system_version_ref=system_version_ref,
        producer_id=producer_id,
        request_model=request_model,
        response_model=response_model,
        finish_reason=finish_reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            response = await client.post(
                ingest_url,
                headers={
                    "authorization": f"Bearer {ingest_token}",
                    "content-type": "application/json",
                },
                json=body,
            )
        if not response.is_success:
            logger.warning("CRUX observation rejected with HTTP %s", response.status_code)
    except Exception as exc:
        # CRUX telemetry must never determine Ask availability.
        logger.warning("CRUX observation delivery skipped: %s", type(exc).__name__)
