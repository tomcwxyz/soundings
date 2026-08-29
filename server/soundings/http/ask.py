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

    # ── Conversation handling ──────────────────────────────────────
    conversation_store = getattr(request.app.state, "conversation_store", None)
    prior_messages: list[dict[str, Any]] | None = None
    conversation_id: str | None = None
    stored_place_id: str | None = None

    if input.conversation_id and conversation_store:
        conv = conversation_store.get(input.conversation_id)
        if conv is not None:
            conversation_id = input.conversation_id
            prior_messages = conv.messages
            stored_place_id = conv.place_id

    # New conversation (or conversation not found — start fresh)
    if conversation_id is None and conversation_store:
        conversation_id = conversation_store.create(place_id=input.place_id)

    # For follow-ups, use the stored place_id if the client didn't send one.
    # The first question may have resolved a place via find_place (no place_id
    # in the request), and we need to maintain that context.
    effective_place_id = input.place_id or stored_place_id

    # Build place context if we have a place_id (from request or conversation)
    place_name: str | None = None
    if effective_place_id:
        from sqlalchemy import text

        async with request.app.state.engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT name FROM geography.place WHERE id = :id"),
                    {"id": effective_place_id},
                )
            ).first()
        if row:
            place_name = row.name

    prompt_builder = SystemPromptBuilder(
        place_name=place_name,
        place_id=effective_place_id,
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

        # Run the orchestrator in the background. First-turn cached answers
        # include their full message/tool history, so they can be replayed
        # instantly while still seeding a normal follow-up conversation.
        task = asyncio.create_task(
            orchestrator.run(
                input.query,
                callback,
                prior_messages=prior_messages,
            )
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
            conversation_store.set_messages(conversation_id, result_messages)
            # If the conversation doesn't have a place_id yet, try to extract
            # one from the tool results (e.g. find_place resolved the place).
            if not conversation_store.get(conversation_id).place_id:
                extracted = _extract_place_id(result_messages)
                if extracted:
                    conversation_store.update_place_id(conversation_id, extracted)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _extract_place_id(messages: list[dict[str, Any]]) -> str | None:
    """Try to find a place_id from tool results in the message history.

    Looks for find_place tool results first (most reliable), then falls back
    to any tool result that contains a place_id field. Returns the first
    match found — the first question's place is the most relevant.
    """
    import json

    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            raw = block.get("content", "")
            if not isinstance(raw, str):
                continue
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            # find_place returns a list of place objects or a single place
            if isinstance(parsed, dict):
                pid = parsed.get("id") or parsed.get("place_id")
                if pid and isinstance(pid, str) and ":" in pid:
                    return pid
            elif isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        pid = item.get("id") or item.get("place_id")
                        if pid and isinstance(pid, str) and ":" in pid:
                            return pid
    return None
