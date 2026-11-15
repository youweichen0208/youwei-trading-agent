import { z } from "zod";

export interface RuntimeEvent {
  sequence: number;
  kind: string;
  text?: string;
  tool?: string;
  /** Size-capped upstream preview, used only to derive a whitelisted summary. */
  preview?: string;
  durationSeconds?: number;
}
export interface EventBatch {
  events: RuntimeEvent[];
  gap: boolean;
  closed: boolean;
  limited: boolean;
}
const envelope = z.object({
  run_id: z.string(),
  event: z.string().min(1).max(100),
  seq: z.number().int().min(0).max(Number.MAX_SAFE_INTEGER).optional(),
});

// Bounded SSE sampling lets the durable Worker yield its lease, poll stop/state,
// and schedule specialists. Cursor advancement happens only in the DB commit.
// The window is short so answer deltas reach the browser while the model is still
// speaking instead of arriving as one paragraph-sized burst.
export async function readRunEvents(
  transport: typeof fetch,
  origin: string,
  token: string,
  runId: string,
  after: number,
  durationMs = 500,
): Promise<EventBatch> {
  if (!Number.isSafeInteger(after) || after < -1)
    throw new Error("invalid_event_cursor");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), durationMs);
  const batch: EventBatch = {
    events: [],
    gap: false,
    closed: false,
    limited: false,
  };
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  let cursor = after;
  try {
    const response = await transport(
      `${origin}/v1/runs/${encodeURIComponent(runId)}/events`,
      {
        redirect: "error",
        signal: controller.signal,
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "text/event-stream",
          ...(after >= 0 ? { "Last-Event-ID": String(after) } : {}),
        },
      },
    );
    if (response.status === 404) {
      await response.body?.cancel();
      return { ...batch, gap: true };
    }
    if (
      !response.ok ||
      !response.headers.get("content-type")?.startsWith("text/event-stream")
    ) {
      await response.body?.cancel();
      throw new Error("upstream_event_response_invalid");
    }
    reader = response.body?.getReader();
    if (!reader) throw new Error("upstream_event_response_invalid");
    const decoder = new TextDecoder("utf-8", { fatal: true });
    let buffer = "",
      total = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.length;
      if (total > 1_048_576) {
        if (!batch.events.length)
          throw new Error("upstream_event_response_too_large");
        batch.limited = true;
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      for (;;) {
        const boundary = /\r?\n\r?\n/.exec(buffer);
        if (!boundary) break;
        const frame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        if (frame.length > 65_536)
          throw new Error("upstream_event_frame_too_large");
        const lines = frame.split(/\r?\n/);
        if (lines.includes(": stream closed")) batch.closed = true;
        const data = lines
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).replace(/^ /, ""))
          .join("\n");
        if (!data) continue;
        const raw = JSON.parse(data);
        const parsed = envelope.parse(raw);
        if (parsed.run_id !== runId)
          throw new Error("upstream_event_identity_mismatch");
        if (parsed.event === "replay.truncated") {
          batch.gap = true;
          continue;
        }
        const id = lines
          .find((line) => line.startsWith("id:"))
          ?.slice(3)
          .trim();
        if (parsed.seq === undefined || id !== String(parsed.seq))
          throw new Error("upstream_event_cursor_invalid");
        if (parsed.seq <= cursor) continue;
        if (parsed.seq !== cursor + 1) batch.gap = true;
        cursor = parsed.seq;
        const event: RuntimeEvent = { sequence: cursor, kind: parsed.event };
        // Only bounded text deltas, tool identity and a size-capped preview
        // travel further. The preview is reduced server-side to a whitelisted
        // summary and never stored verbatim; reasoning, provider errors and
        // raw terminal payloads are never archived.
        if (parsed.event === "message.delta") {
          if (typeof raw.delta !== "string" || raw.delta.length > 32_768)
            throw new Error("upstream_event_text_invalid");
          event.text = raw.delta.replaceAll(token, "[redacted]");
        } else if (
          [
            "message.interim",
            "reasoning.available",
            "reasoning.delta",
          ].includes(parsed.event)
        ) {
          // Upstream-provided commentary / lossy reasoning preview. Raw private
          // reasoning never reaches the run stream; this is bounded and
          // redacted before it is archived.
          if (typeof raw.text === "string" && raw.text.length <= 32_768) {
            const text = raw.text
              .replaceAll(token, "[redacted]")
              .slice(0, 2000)
              .trim();
            if (text) event.text = text;
          }
        } else if (["tool.started", "tool.completed"].includes(parsed.event)) {
          if (
            typeof raw.tool === "string" &&
            /^[a-z][a-z0-9_]{0,63}$/.test(raw.tool)
          )
            event.tool = raw.tool;
          if (typeof raw.preview === "string")
            event.preview = raw.preview
              .replaceAll(token, "[redacted]")
              .slice(0, 2048);
          if (
            typeof raw.duration === "number" &&
            Number.isFinite(raw.duration) &&
            raw.duration >= 0 &&
            raw.duration <= 86_400
          )
            event.durationSeconds = raw.duration;
        }
        batch.events.push(event);
        if (batch.events.length === 256) {
          batch.limited = true;
          return batch;
        }
      }
      if (buffer.length > 65_536)
        throw new Error("upstream_event_frame_too_large");
      if (batch.closed) break;
    }
    return batch;
  } catch (error) {
    // A bounded sample may end mid-frame. Its uncommitted bytes are replayed
    // next time from the last fully parsed event, never guessed or skipped.
    if (controller.signal.aborted) return batch;
    throw error;
  } finally {
    clearTimeout(timer);
    controller.abort();
    await reader?.cancel().catch(() => undefined);
    reader?.releaseLock();
  }
}
