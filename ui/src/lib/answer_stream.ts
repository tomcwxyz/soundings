// SSE client for the Soundings /v1/ask streaming endpoint.
//
// The server emits Server-Sent-Events frames of the shape:
//   data: {"type":"status","message":"Looking up place…"}\n\n
//   data: {"type":"block","block":{…}}\n\n
//   data: {"type":"sources","sources":[…]}\n\n
//   data: {"type":"done"}\n\n
//
// Each frame is a single JSON object on a "data: " prefixed line, with
// frames delimited by a blank line (the SSE convention). `parseSSEStream`
// is a pure helper used by tests and by `streamAsk`, the browser-side
// reader that consumes the streaming fetch body chunk-by-chunk.

export interface AnswerBlock {
  type: string;
  [key: string]: unknown;
}

export interface SourceRef {
  source_id: string;
  source_label: string;
  publisher: string;
  publisher_url?: string;
  dataset_url?: string;
  retrieved_at?: string;
  cache_status?: string;
  licence?: string;
}

export type AskEvent =
  | { type: "conversation"; conversation_id: string }
  | { type: "status"; message: string }
  | { type: "block"; block: AnswerBlock }
  | { type: "sources"; sources: SourceRef[] }
  | { type: "done" }
  | { type: "error"; message: string };

/**
 * Parse a raw SSE payload into a list of AskEvents.
 *
 * Splits the input on newlines, ignores blank lines and comment lines
 * (those starting with ":"), and decodes lines prefixed with "data: "
 * as JSON. Unknown event types are skipped rather than thrown, so a
 * partial stream can still be rendered.
 */
export function parseSSEStream(raw: string): AskEvent[] {
  const events: AskEvent[] = [];
  const lines = raw.split("\n");
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.length === 0) continue;
    if (trimmed.startsWith(":")) continue;
    if (!trimmed.startsWith("data:")) continue;
    const payload = trimmed.slice(5).trimStart();
    if (payload.length === 0) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(payload);
    } catch {
      continue;
    }
    if (typeof parsed !== "object" || parsed === null) continue;
    const evt = parsed as Record<string, unknown>;
    const t = typeof evt.type === "string" ? evt.type : "";
    if (
      t === "conversation" &&
      typeof evt.conversation_id === "string"
    ) {
      events.push({
        type: "conversation",
        conversation_id: evt.conversation_id,
      });
    } else if (t === "status" && typeof evt.message === "string") {
      events.push({ type: "status", message: evt.message });
    } else if (t === "block" && typeof evt.block === "object" && evt.block !== null) {
      events.push({ type: "block", block: evt.block as AnswerBlock });
    } else if (t === "sources" && Array.isArray(evt.sources)) {
      events.push({ type: "sources", sources: evt.sources as SourceRef[] });
    } else if (t === "done") {
      events.push({ type: "done" });
    } else if (t === "error" && typeof evt.message === "string") {
      events.push({ type: "error", message: evt.message });
    }
    // Unknown event types are silently skipped.
  }
  return events;
}

/**
 * Streaming reader for the /v1/ask endpoint.
 *
 * Opens a fetch POST, acquires a reader on `response.body`, and buffers
 * chunks. Because SSE frames are delimited by a blank line ("\n\n"),
 * a single network chunk may contain several frames or a partial frame;
 * we split on "\n\n", process complete frames, and carry any trailing
 * fragment into the next iteration. Each parsed AskEvent is forwarded to
 * `onEvent`.
 *
 * Resolves when the stream ends (server closes the body). If `fetch`
 * throws or the response is not ok, the error is forwarded to `onEvent`
 * as an "error" event before rejecting.
 */
export async function streamAsk(
  url: string,
  body: unknown,
  onEvent: (event: AskEvent) => void,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(body),
      credentials: "include",
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    onEvent({ type: "error", message });
    throw err;
  }

  if (!response.ok || response.body === null) {
    const message = `${response.status} ${response.statusText}`;
    onEvent({ type: "error", message });
    throw new Error(message);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // Split on the SSE frame boundary. A trailing fragment (after the
      // last "\n\n") is held back for the next chunk.
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        for (const event of parseSSEStream(frame)) {
          onEvent(event);
        }
      }
    }
    // Flush any trailing bytes that lacked a final "\n\n".
    const tail = buffer.trim();
    if (tail.length > 0) {
      for (const event of parseSSEStream(tail)) {
        onEvent(event);
      }
    }
  } finally {
    reader.releaseLock();
  }
}
