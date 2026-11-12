export interface TaskEvent {
  event: string;
  taskId?: string;
  sequence?: number;
  data: any;
}
interface StreamOptions {
  transport?: typeof fetch;
  idleMs?: number;
  retryMs?: number;
  maxRetryMs?: number;
  maxFrameChars?: number;
}
class FatalStreamError extends Error {}
const terminal = (status: unknown) =>
  ["completed", "failed", "cancelled", "interrupted"].includes(String(status));

function delay(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve) => {
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      globalThis.removeEventListener?.("online", finish);
      resolve();
    };
    const timer = setTimeout(finish, ms);
    signal.addEventListener("abort", finish, { once: true });
    globalThis.addEventListener?.("online", finish, { once: true });
    if (signal.aborted) finish();
  });
}

async function readChunk(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  signal: AbortSignal,
  idleMs: number,
) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let cancel: () => void = () => {};
  try {
    return await Promise.race([
      reader.read(),
      new Promise<never>((_, reject) => {
        cancel = () => reject(new Error("stream_interrupted"));
        timer = setTimeout(cancel, idleMs);
        signal.addEventListener("abort", cancel, { once: true });
        if (signal.aborted) cancel();
      }),
    ]);
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", cancel);
  }
}

function decodeFrame(frame: string): {
  kind: string;
  id?: number;
  value?: any;
} {
  let kind = "message",
    rawId: string | undefined;
  const data: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith(":")) continue;
    const colon = line.indexOf(":"),
      field = colon < 0 ? line : line.slice(0, colon);
    const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "data") data.push(value);
    else if (field === "event") kind = value;
    else if (field === "id") rawId = value;
  }
  if (!data.length) return { kind };
  if (kind === "connection.error") throw new Error("connection_lost");
  if (
    rawId === undefined ||
    !/^\d+$/.test(rawId) ||
    !Number.isSafeInteger(Number(rawId))
  )
    throw new FatalStreamError("invalid_event_stream");
  try {
    return { kind, id: Number(rawId), value: JSON.parse(data.join("\n")) };
  } catch {
    throw new FatalStreamError("invalid_event_stream");
  }
}

/** Reconnect only the event subscription. Never submit or stop a task here. */
export async function* subscribeTask(
  taskId: string,
  signal: AbortSignal,
  options: StreamOptions = {},
): AsyncGenerator<TaskEvent> {
  const transport = options.transport ?? fetch;
  const idleMs = options.idleMs ?? 15_000;
  const retryMs = options.retryMs ?? 500,
    maxRetryMs = options.maxRetryMs ?? 10_000;
  const maxFrameChars = options.maxFrameChars ?? 16 * 1024 * 1024;
  let cursor: number | undefined,
    failures = 0,
    reconnecting = false;
  while (!signal.aborted) {
    const connection = new AbortController();
    const abort = () => connection.abort();
    signal.addEventListener("abort", abort, { once: true });
    globalThis.addEventListener?.("offline", abort, { once: true });
    if (signal.aborted) abort();
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    let connectTimer: ReturnType<typeof setTimeout> | undefined;
    let madeProgress = false;
    try {
      connectTimer = setTimeout(abort, idleMs);
      const response = await transport(
        `/api/tasks/${encodeURIComponent(taskId)}/events`,
        {
          signal: connection.signal,
          cache: "no-store",
          headers: {
            Accept: "text/event-stream",
            ...(cursor === undefined
              ? {}
              : { "Last-Event-ID": String(cursor) }),
          },
        },
      );
      clearTimeout(connectTimer);
      if (!response.ok) {
        await response.body?.cancel();
        if ([401, 403, 404].includes(response.status))
          throw new FatalStreamError(
            response.status === 401 ? "unauthorized" : "not_found",
          );
        if (
          response.status < 500 &&
          response.status !== 408 &&
          response.status !== 429
        )
          throw new FatalStreamError("connection_failed");
        throw new Error("connection_failed");
      }
      if (
        !response.body ||
        !response.headers.get("content-type")?.startsWith("text/event-stream")
      ) {
        await response.body?.cancel();
        throw new FatalStreamError("invalid_event_stream");
      }
      reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "",
        snapshotReceived = cursor !== undefined;
      for (;;) {
        const chunk = await readChunk(reader, signal, idleMs);
        if (signal.aborted) return;
        if (chunk.done) throw new Error("connection_lost");
        buffer = (
          buffer + decoder.decode(chunk.value, { stream: true })
        ).replace(/\r\n/g, "\n");
        if (buffer.length > maxFrameChars)
          throw new FatalStreamError("event_stream_too_large");
        let split: number;
        while ((split = buffer.indexOf("\n\n")) >= 0) {
          const frame = decodeFrame(buffer.slice(0, split));
          buffer = buffer.slice(split + 2);
          if (frame.value === undefined) continue;
          const event = frame.value as TaskEvent;
          if (
            !event ||
            event.taskId !== taskId ||
            event.sequence !== frame.id ||
            event.event !== frame.kind
          )
            throw new FatalStreamError("invalid_event_stream");
          const sequence = frame.id!;
          if (event.event === "state.snapshot") {
            if (
              event.data?.task?.id !== taskId ||
              event.data?.lastSequence !== sequence ||
              typeof event.data.task.status !== "string"
            )
              throw new FatalStreamError("invalid_event_stream");
            if (cursor !== undefined && sequence < cursor)
              throw new Error("snapshot_behind_cursor");
            snapshotReceived = true;
          } else {
            if (!snapshotReceived) throw new Error("snapshot_required");
            if (cursor !== undefined && sequence <= cursor) continue;
            if (cursor !== undefined && sequence !== cursor + 1)
              throw new Error("event_gap");
          }
          madeProgress ||= cursor === undefined || sequence > cursor;
          cursor = sequence;
          if (reconnecting) {
            reconnecting = false;
            yield { event: "connection.restored", data: {} };
          }
          yield event;
          if (
            event.event === "state.snapshot"
              ? terminal(event.data.task.status)
              : !event.data?.childTaskId && terminal(event.data?.status)
          )
            return;
        }
      }
    } catch (error) {
      if (signal.aborted) return;
      if (error instanceof FatalStreamError) throw error;
    } finally {
      clearTimeout(connectTimer);
      signal.removeEventListener("abort", abort);
      globalThis.removeEventListener?.("offline", abort);
      connection.abort();
      if (reader) {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
      }
    }
    if (signal.aborted) return;
    reconnecting = true;
    yield { event: "connection.reconnecting", data: {} };
    failures = madeProgress ? 0 : Math.min(failures + 1, 10);
    await delay(Math.min(maxRetryMs, retryMs * 2 ** failures), signal);
  }
}
