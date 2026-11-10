"""AskOrchestrator — runs the Claude tool-use loop and streams SSE events.

The loop:
1. Send the system prompt + user question to Claude with tool definitions.
2. Claude responds with content blocks (text + tool_use).
3. Dispatch each tool_use to the in-process handler via ToolDispatcher.
4. Feed results back to Claude as tool_result messages.
5. Repeat until Claude calls compose_answer (terminal) or max iterations.
6. Stream SSE events: status (per tool call), block (from compose_answer),
   sources (deduped SourceRefs), done.

Uses the non-streaming ``messages.create()`` API — we need full tool_use
blocks before dispatching, so streaming the model response adds complexity
without benefit. The SSE streaming to the UI is handled by the caller,
which receives events via the callback.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from anthropic import Anthropic
from anthropic.types import ThinkingConfigAdaptiveParam

from soundings.ask.blocks import TextBlock
from soundings.ask.dispatcher import ToolDispatcher
from soundings.ask.grants_guidance import GRANTS_GUIDANCE
from soundings.ask.prompts import SystemPromptBuilder
from soundings.cache.answer_cache import AnswerCacheStore

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 12
# Sonnet 5 runs adaptive thinking by default, and its new tokenizer emits ~30%
# more tokens than 4.6 — thinking now shares this budget with the response, so a
# cap tuned for 4.6 (8192) truncates a rich compose_answer mid-JSON. 16k leaves
# headroom for thinking plus a full multi-block answer.
MAX_TOKENS_OUTPUT = 16000
# Adaptive thinking is the default on Sonnet 5; we send it explicitly so the
# config is legible and survives a model swap. Thinking blocks it returns MUST
# be echoed back in the assistant turn (see _loop) or the next call is rejected.
THINKING_CONFIG: ThinkingConfigAdaptiveParam = {"type": "adaptive"}
# Whole-request budget. Multi-tool answers (a place overview can touch the
# profile, civil-society, several indicators and a comparison) legitimately
# take over a minute when upstreams are cold, and adaptive thinking adds to
# per-turn latency, so allow generous headroom. Effective only because the
# blocking LLM calls run via asyncio.to_thread.
REQUEST_TIMEOUT_SECONDS = 180

SSECallback = Callable[[dict[str, Any]], Awaitable[None] | None]


def get_anthropic_client(api_key: str) -> Anthropic:
    """Factory so tests can patch the client."""
    return Anthropic(api_key=api_key)


class AskOrchestrator:
    """Runs the Claude tool-use loop, streaming events via callback."""

    def __init__(
        self,
        *,
        dispatcher: ToolDispatcher,
        prompt_builder: SystemPromptBuilder,
        api_key: str,
        model: str,
        max_iterations: int = MAX_ITERATIONS,
        answer_cache: AnswerCacheStore | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._prompt_builder = prompt_builder
        self._api_key = api_key
        self._model = model
        self._max_iterations = max_iterations
        self._answer_cache = answer_cache

    async def run(
        self,
        query: str,
        callback: SSECallback,
        *,
        prior_messages: list[dict[str, Any]] | None = None,
        skip_cache: bool = False,
    ) -> list[dict[str, Any]] | None:
        """Run the tool-use loop, streaming events via callback.

        If ``prior_messages`` is provided (multi-turn follow-up), the loop
        continues from the existing message history. Otherwise a fresh
        conversation starts with just the user query.

        If ``skip_cache`` is True, the answer cache is bypassed — used when
        a conversation store is active so the full message history (needed
        for follow-ups) is always captured.

        If an answer cache is configured and a fresh entry exists for
        this query, replay the cached events without calling Claude and return
        its stored message/tool history so a normal follow-up conversation can
        continue from the cached answer. Legacy cache entries without message
        history are treated as misses.

        Returns the full messages list on success (for conversation
        storage), or None on error/timeout.
        """
        # ── Cache check (first questions only — follow-ups are contextual).
        # Skip when explicitly requested (conversation store is active).
        if self._answer_cache is not None and prior_messages is None and not skip_cache:
            place_id = self._prompt_builder.place_id
            try:
                cached = await self._answer_cache.get(query, place_id)
            except Exception:
                # Cache availability must never determine Ask availability.
                # During rolling deploys the schema may briefly lag the app,
                # and transient DB failures should simply fall through to a
                # normal model/tool run.
                logger.warning("Answer cache read failed; treating as miss", exc_info=True)
                cached = None
            if cached is not None and cached.messages is not None:
                logger.info("Answer cache HIT")
                for event in cached.events:
                    await _emit(callback, event)
                return cached.messages
            if cached is not None:
                logger.info("Answer cache legacy entry ignored")

        # ── Cache miss — run the Claude loop ─────────────────────────
        client = get_anthropic_client(self._api_key)
        system_prompt = self._prompt_builder.build() + "\n\n" + GRANTS_GUIDANCE
        tool_specs = self._dispatcher.tool_specs()

        # Start from prior messages (follow-up) or a fresh user turn.
        if prior_messages:
            messages: list[dict[str, Any]] = list(prior_messages)
            messages.append({"role": "user", "content": query})
        else:
            messages = [{"role": "user", "content": query}]

        # Collect events for caching if the run succeeds
        collected_events: list[dict[str, Any]] | None = [] if self._answer_cache else None

        # Wrap the callback so we can collect events for caching without
        # changing every _emit call inside _loop.
        collecting_callback: SSECallback = callback
        if collected_events is not None:

            async def collecting_callback(event: dict[str, Any]) -> None:
                collected_events.append(event)
                result = callback(event)
                if asyncio.iscoroutine(result):
                    await result

        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                await self._loop(
                    client,
                    system_prompt,
                    tool_specs,
                    messages,
                    collecting_callback,
                )
            # Run succeeded — store the collected events in the answer cache.
            # Only cache if we got a "done" event (not an error/timeout).
            # Skip caching for follow-ups (contextual — same query in different
            # conversations yields different answers).
            if (
                collected_events is not None
                and prior_messages is None
                and self._answer_cache is not None
                and any(e.get("type") == "done" for e in collected_events)
            ):
                try:
                    await self._answer_cache.put(
                        query,
                        self._prompt_builder.place_id,
                        collected_events,
                        messages=messages,
                    )
                    logger.info("Answer cache STORED")
                except Exception:
                    # Cache write failure shouldn't fail the response
                    logger.warning("Answer cache store failed", exc_info=True)
            return messages
        except TimeoutError:
            await _emit(callback, {"type": "error", "message": "Request timed out"})
            return None
        except Exception as e:
            logger.exception("Ask orchestrator error")
            await _emit(callback, {"type": "error", "message": str(e)})
            return None

    async def _loop(
        self,
        client: Anthropic,
        system_prompt: str,
        tool_specs: list[dict[str, object]],
        messages: list[dict[str, Any]],
        callback: SSECallback,
    ) -> None:
        # The system prompt + tool list are a large, static prefix re-sent on
        # every one of up to MAX_ITERATIONS turns. A cache breakpoint on the
        # system block caches the whole tools+system prefix (render order is
        # tools -> system -> messages), so iterations 2..N read it at ~0.1x.
        system_blocks = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        grounding_revised = False
        for _iteration in range(self._max_iterations):
            # The Anthropic client is synchronous; calling it directly would
            # block the event loop for the whole request, stalling the SSE
            # stream and rendering `asyncio.timeout` (in run()) ineffective.
            # Run it in a worker thread so the loop stays responsive.
            model_started = time.perf_counter()
            response = await asyncio.to_thread(
                lambda: client.messages.create(
                    model=self._model,
                    max_tokens=MAX_TOKENS_OUTPUT,
                    thinking=THINKING_CONFIG,
                    system=system_blocks,  # type: ignore[arg-type]
                    tools=tool_specs,  # type: ignore[arg-type]
                    messages=messages,  # type: ignore[arg-type]
                )
            )
            usage = getattr(response, "usage", None)
            logger.info(
                "ask_model_turn iteration=%d latency_ms=%d input_tokens=%s "
                "output_tokens=%s cache_read_tokens=%s cache_write_tokens=%s",
                _iteration + 1,
                round((time.perf_counter() - model_started) * 1000),
                getattr(usage, "input_tokens", None),
                getattr(usage, "output_tokens", None),
                getattr(usage, "cache_read_input_tokens", None),
                getattr(usage, "cache_creation_input_tokens", None),
            )

            # A cybersecurity/safety classifier can decline a request: HTTP 200
            # with stop_reason "refusal" and (usually) empty content. Vanishingly
            # rare for UK place statistics, but handle it rather than emit silence.
            if getattr(response, "stop_reason", None) == "refusal":
                await _emit(
                    callback,
                    {
                        "type": "block",
                        "block": {
                            "type": "text",
                            "markdown": (
                                "Sorry — I can't answer that question. Try summarising a"
                                " place or comparing two."
                            ),
                        },
                    },
                )
                await _emit(callback, {"type": "done"})
                return

            # Collect content blocks from the response
            assistant_content: list[dict[str, Any]] = []
            tool_use_blocks: list[dict[str, Any]] = []

            for content_block in response.content:
                # Thinking blocks must be echoed back verbatim (with signature,
                # in their original position ahead of the tool_use they produced)
                # or the next call is rejected. We preserve them but don't emit
                # them to the UI — the status steps carry progress instead.
                if content_block.type == "thinking":
                    assistant_content.append(
                        {
                            "type": "thinking",
                            "thinking": content_block.thinking,
                            "signature": content_block.signature,
                        }
                    )
                elif content_block.type == "redacted_thinking":
                    assistant_content.append(
                        {
                            "type": "redacted_thinking",
                            "data": content_block.data,
                        }
                    )
                elif content_block.type == "text":
                    assistant_content.append(
                        {
                            "type": "text",
                            "text": content_block.text,
                        }
                    )
                elif content_block.type == "tool_use":
                    tool_use_blocks.append(
                        {
                            "type": "tool_use",
                            "id": content_block.id,
                            "name": content_block.name,
                            "input": content_block.input,
                        }
                    )
                    assistant_content.append(
                        {
                            "type": "tool_use",
                            "id": content_block.id,
                            "name": content_block.name,
                            "input": content_block.input,
                        }
                    )

            messages.append({"role": "assistant", "content": assistant_content})

            if not tool_use_blocks:
                # Some model responses skip compose_answer. They still pass
                # through the same sanitisation and grounding check.
                raw = "\n\n".join(
                    b.text for b in response.content if b.type == "text" and b.text.strip()
                )
                if raw:
                    finalised = await self._dispatcher.finalise_answer(
                        {"blocks": [{"type": "text", "markdown": raw}]}
                    )
                    safe_text = (
                        "I couldn't verify the figures needed for this answer, "
                        "so I have not published them. Please try a narrower question."
                        if finalised.untraced else finalised.answer.blocks[0].markdown
                    )
                    await _emit(
                        callback,
                        {"type": "block", "block": {"type": "text", "markdown": safe_text}},
                    )
                    await _emit(
                        callback,
                        {"type": "grounding", "untraced": finalised.untraced,
                         "revised": False},
                    )
                await _emit(
                    callback,
                    {
                        "type": "sources",
                        "sources": [s.model_dump(mode="json") for s in self._dispatcher.sources],
                    },
                )
                await _emit(callback, {"type": "done"})
                return

            # Dispatch each tool call
            tool_results: list[dict[str, Any]] = []
            for tb in tool_use_blocks:
                name = tb["name"]
                tool_input = tb["input"]

                if self._dispatcher.is_terminal_tool(name):
                    finalised = await self._dispatcher.finalise_answer(tool_input)
                    if finalised.untraced and not grounding_revised:
                        # One chance to fix figures no tool returned before
                        # anything reaches the reader.
                        grounding_revised = True
                        logger.info("ask_grounding_revision untraced=%d", len(finalised.untraced))
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tb["id"],
                                "is_error": True,
                                "content": _revision_request(finalised.untraced),
                            }
                        )
                        continue

                    blocks = list(finalised.answer.blocks)
                    if finalised.untraced:
                        # Never publish an unsupported claim merely because
                        # the model's one correction attempt failed.
                        blocks = [
                            block for block in blocks
                            if not self._dispatcher.untraced_block(block)
                        ]
                        blocks.append(
                            TextBlock(
                                type="text",
                                markdown=(
                                    "Some claims were omitted because their figures "
                                    "could not be verified against the available data."
                                ),
                            )
                        )
                    for block in blocks:
                        await _emit(
                            callback,
                            {
                                "type": "block",
                                "block": block.model_dump(mode="json"),
                            },
                        )

                    # Append a synthetic tool_result for compose_answer (and
                    # any earlier non-terminal tools in this iteration) so the
                    # message history is valid for follow-up questions.
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tb["id"],
                            "content": "Answer composed and streamed to user.",
                        }
                    )
                    messages.append({"role": "user", "content": tool_results})

                    sources = [s.model_dump(mode="json") for s in self._dispatcher.sources]
                    await _emit(callback, {"type": "sources", "sources": sources})
                    await _emit(
                        callback,
                        {
                            "type": "grounding",
                            "untraced": finalised.untraced,
                            "revised": grounding_revised,
                        },
                    )
                    await _emit(callback, {"type": "done"})
                    return

                # Non-terminal tool — dispatch and emit status
                await _emit(callback, {"type": "status", "message": f"Calling {name}…"})
                tool_started = time.perf_counter()
                succeeded = True
                try:
                    result = await self._dispatcher.dispatch(name, tool_input)
                except Exception as e:
                    succeeded = False
                    logger.warning("Tool %s failed: %s", name, e)
                    result = {"error": str(e)}

                payload = json.dumps(result)
                logger.info(
                    "ask_tool_call tool=%s latency_ms=%d payload_bytes=%d success=%s",
                    name,
                    round((time.perf_counter() - tool_started) * 1000),
                    len(payload.encode("utf-8")),
                    succeeded,
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tb["id"],
                        "content": payload,
                    }
                )

            messages.append({"role": "user", "content": tool_results})
            continue

        # Max iterations exceeded. Append tool_results for any pending
        # tool_use blocks so the message history stays valid if the caller
        # stores it anyway (the error path means this is unlikely to be
        # useful, but defensive correctness is cheap here).
        if tool_use_blocks:
            for tb in tool_use_blocks:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tb["id"],
                        "content": "Tool not completed (max iterations exceeded).",
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        await _emit(
            callback,
            {
                "type": "error",
                "message": f"Exceeded max iterations ({self._max_iterations})",
            },
        )


def _revision_request(untraced: list[str]) -> str:
    return (
        "GROUNDING_CHECK_FAILED: these figures in your answer do not match any value "
        "returned by a tool in this conversation: "
        + ", ".join(untraced)
        + ". Every number you state must come from a tool result. If it is a share, "
        "an average across places, a difference or a ranking, compute it with "
        "calculate_share, calculate_weighted_average or calculate_difference and "
        "quote the result; otherwise remove the figure or describe it without a "
        "number. Then call compose_answer again."
    )


async def _emit(callback: SSECallback, event: dict[str, Any]) -> None:
    """Call the callback, awaiting if it returns a coroutine."""
    result = callback(event)
    if asyncio.iscoroutine(result):
        await result
