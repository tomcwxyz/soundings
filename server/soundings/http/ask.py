"""HTTP route for /v1/ask — the natural-language ask interface.

POST /v1/ask with {query, place_id?} → SSE stream of events.
"""

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from soundings.ask.dispatcher import ToolDispatcher
from soundings.ask.orchestrator import REQUEST_TIMEOUT_SECONDS, AskOrchestrator
from soundings.ask.prompts import SystemPromptBuilder
from soundings.core.config import get_settings

router = APIRouter(prefix="/v1")

# Per-event SSE wait: a backstop for a genuinely wedged background task (e.g.
# a stuck upstream), not a total-request budget. Derived from the
# orchestrator's own REQUEST_TIMEOUT_SECONDS (rather than a separate literal)
# so the two can't silently drift apart again — that happened when adaptive
# thinking pushed REQUEST_TIMEOUT_SECONDS from 120 to 180 without this value
# following, which killed the slowest (and richest) answers with a spurious
# "Stream timeout" before the orchestrator's own timeout/completion could fire.
SSE_WATCHDOG_SECONDS = REQUEST_TIMEOUT_SECONDS + 10.0


class AskInput(BaseModel):
    query: str
    place_id: str | None = None
    conversation_id: str | None = None


@router.post("/ask")
async def ask(input: AskInput, request: Request) -> StreamingResponse:
    if not input.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="Anthropic API key not configured")

    # Build place context if a place_id is provided
    place_name: str | None = None
    if input.place_id:
        from sqlalchemy import text

        async with request.app.state.engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT name FROM geography.place WHERE id = :id"),
                    {"id": input.place_id},
                )
            ).first()
        if row:
            place_name = row.name

    # ── Conversation handling ──────────────────────────────────────
    conversation_store = getattr(request.app.state, "conversation_store", None)
    prior_messages: list[dict[str, Any]] | None = None
    conversation_id: str | None = None

    if input.conversation_id and conversation_store:
        conv = conversation_store.get(input.conversation_id)
        if conv is not None:
            conversation_id = input.conversation_id
            prior_messages = conv.messages

    # New conversation (or conversation not found — start fresh)
    if conversation_id is None and conversation_store:
        conversation_id = conversation_store.create(place_id=input.place_id)

    prompt_builder = SystemPromptBuilder(
        place_name=place_name,
        place_id=input.place_id,
        is_follow_up=prior_messages is not None,
    )

    dispatcher = ToolDispatcher(request.app.state)

    answer_cache = getattr(request.app.state, "answer_cache", None)

    orchestrator = AskOrchestrator(
        dispatcher=dispatcher,
        prompt_builder=prompt_builder,
        api_key=settings.anthropic_api_key,
        model=settings.ask_model,
        answer_cache=answer_cache,
    )

    async def event_stream() -> Any:
        queue: asyncio.Queue[str] = asyncio.Queue()

        async def callback(event: dict[str, Any]) -> None:
            await queue.put(json.dumps(event))

        # Emit conversation_id as the first event so the client can
        # reference it in follow-up requests.
        if conversation_id:
            await queue.put(
                json.dumps(
                    {
                        "type": "conversation",
                        "conversation_id": conversation_id,
                    }
                )
            )

        # Run the orchestrator in the background
        task = asyncio.create_task(
            orchestrator.run(input.query, callback, prior_messages=prior_messages)
        )

        # Stream events as SSE. The per-event wait is a backstop set just above
        # the orchestrator's own REQUEST_TIMEOUT_SECONDS budget, so the
        # orchestrator emits "error"/"done" first and this only fires if the
        # background task is genuinely wedged (e.g. a stuck upstream).
        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=SSE_WATCHDOG_SECONDS)
                yield f"data: {data}\n\n"
                event_obj = json.loads(data)
                if event_obj.get("type") in ("done", "error"):
                    break
            except TimeoutError:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Stream timeout'})}\n\n"
                break

        # Ensure the task completes and store messages for follow-ups
        result_messages = await task
        if conversation_id and conversation_store and result_messages:
            conversation_store.append_messages(conversation_id, result_messages)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
