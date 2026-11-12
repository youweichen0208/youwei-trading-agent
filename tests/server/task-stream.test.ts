import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import {
  subscribeTask,
  type TaskEvent,
} from "../../packages/events-client/src/task-stream.js";

async function collect<T>(events: AsyncIterable<T>): Promise<T[]> {
  const result: T[] = [];
  for await (const event of events) result.push(event);
  return result;
}

const id = "task-test-id";
const envelope = (
  sequence: number,
  event: string,
  data: unknown,
): TaskEvent => ({
  taskId: id,
  sequence,
  event,
  data,
});
const snapshot = (sequence: number, status = "running") =>
  envelope(sequence, "state.snapshot", {
    task: { id, status },
    lastSequence: sequence,
    workspace: { version: sequence },
    messages: [],
  });
const frame = (value: any) =>
  `id: ${value.sequence}\nevent: ${value.event}\ndata: ${JSON.stringify(value)}\n\n`;
const response = (...events: any[]) =>
  new Response(events.map(frame).join(""), {
    headers: { "Content-Type": "text/event-stream" },
  });
const signal = () => AbortSignal.timeout(3000);
const fast = { retryMs: 1, maxRetryMs: 2, idleMs: 100 };

test("real HTTP stream reconnects with cursor, deduplicates events and waits for root terminal", async () => {
  const requests: { method: string | undefined; cursor: string | undefined }[] =
    [];
  const server = createServer((req, res) => {
    requests.push({
      method: req.method,
      cursor: req.headers["last-event-id"] as string | undefined,
    });
    res.setHeader("Content-Type", "text/event-stream");
    if (requests.length === 1) {
      res.end(
        frame(snapshot(1)) +
          frame(envelope(2, "assistant.delta", { text: "one" })),
      );
    } else {
      res.end(
        frame(snapshot(4)) +
          frame(envelope(4, "assistant.delta", { text: "duplicate" })) +
          frame(
            envelope(5, "task.completed", {
              status: "completed",
              childTaskId: "child",
            }),
          ) +
          frame(
            envelope(6, "task.completed", {
              status: "completed",
              output: { text: "final" },
            }),
          ),
      );
    }
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  try {
    const events = await collect(
      subscribeTask(id, signal(), {
        ...fast,
        transport: (url, init) =>
          fetch(`http://127.0.0.1:${address.port}${url}`, init),
      }),
    );
    assert.deepEqual(requests, [
      { method: "GET", cursor: undefined },
      { method: "GET", cursor: "2" },
    ]);
    assert.deepEqual(
      events.filter((e) => e.sequence !== undefined).map((e) => e.sequence),
      [1, 2, 4, 5, 6],
    );
    assert.ok(events.some((e) => e.event === "connection.reconnecting"));
    assert.ok(events.some((e) => e.event === "connection.restored"));
    assert.equal(events.at(-1)?.data.output.text, "final");
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

test("SSE decoder handles CRLF split across UTF-8 chunks and closes on a terminal snapshot", async () => {
  const value = snapshot(0, "completed");
  value.data.messages = [{ content: "研究完成" }];
  const bytes = new TextEncoder().encode(frame(value).replaceAll("\n", "\r\n"));
  let closed = false;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
    },
    cancel() {
      closed = true;
    },
  });
  const events = await collect(
    subscribeTask(id, signal(), {
      ...fast,
      transport: async () =>
        new Response(body, {
          headers: { "Content-Type": "text/event-stream" },
        }),
    }),
  );
  assert.equal(events.length, 1);
  assert.equal(events[0]?.data.messages[0].content, "研究完成");
  assert.equal(closed, true);
});

test("a sequence gap reloads a snapshot without applying the out-of-order mutation", async () => {
  let calls = 0;
  const events = await collect(
    subscribeTask(id, signal(), {
      ...fast,
      transport: async (_, init) => {
        calls++;
        if (calls === 1)
          return response(
            snapshot(1),
            envelope(3, "canvas.upsert", { unsafeGap: true }),
          );
        assert.equal(new Headers(init?.headers).get("last-event-id"), "1");
        return response(snapshot(3, "completed"));
      },
    }),
  );
  assert.equal(calls, 2);
  assert.equal(
    events.some((e) => e.data?.unsafeGap),
    false,
  );
  assert.equal(events.at(-1)?.sequence, 3);
});

test("retained reconnect can replay subsequent events without another snapshot", async () => {
  let calls = 0;
  const events = await collect(
    subscribeTask(id, signal(), {
      ...fast,
      transport: async () =>
        ++calls === 1
          ? response(snapshot(1))
          : response(envelope(2, "run.completed", { status: "completed" })),
    }),
  );
  assert.equal(calls, 2);
  assert.equal(events.filter((e) => e.event === "state.snapshot").length, 1);
  assert.equal(events.at(-1)?.event, "run.completed");
});

test("auth failures and foreign task frames stop subscriptions without retry", async () => {
  for (const result of [
    new Response("", { status: 401 }),
    response({ ...snapshot(1), taskId: "foreign" }),
  ]) {
    let calls = 0;
    await assert.rejects(
      collect(
        subscribeTask(id, signal(), {
          ...fast,
          transport: async () => {
            calls++;
            return result;
          },
        }),
      ),
      /unauthorized|invalid_event_stream/,
    );
    assert.equal(calls, 1);
  }
});

test("idle connections recover and abort during backoff stops further requests", async () => {
  let calls = 0,
    cancelled = false;
  const events = await collect(
    subscribeTask(id, signal(), {
      ...fast,
      idleMs: 15,
      transport: async () => {
        if (++calls > 1) return response(snapshot(2, "completed"));
        return new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(new TextEncoder().encode(frame(snapshot(1))));
            },
            cancel() {
              cancelled = true;
            },
          }),
          { headers: { "Content-Type": "text/event-stream" } },
        );
      },
    }),
  );
  assert.equal(cancelled, true);
  assert.equal(calls, 2);
  assert.equal(events.at(-1)?.sequence, 2);
  const controller = new AbortController();
  let attempted = 0;
  for await (const event of subscribeTask(id, controller.signal, {
    retryMs: 60000,
    transport: async () => {
      attempted++;
      throw new TypeError("offline");
    },
  }))
    if (event.event === "connection.reconnecting") controller.abort();
  assert.equal(attempted, 1);
});

test("transient HTTP failure retries subscription while malformed data fails explicitly", async () => {
  let attempts = 0;
  const events = await collect(
    subscribeTask(id, signal(), {
      ...fast,
      transport: async () =>
        ++attempts < 3
          ? new Response("", { status: 503 })
          : response(snapshot(9, "completed")),
    }),
  );
  assert.equal(attempts, 3);
  assert.equal(events.at(-1)?.sequence, 9);
  await assert.rejects(
    collect(
      subscribeTask(id, signal(), {
        ...fast,
        transport: async () =>
          new Response("id: 1\nevent: state.snapshot\ndata: invalid-json\n\n", {
            headers: { "Content-Type": "text/event-stream" },
          }),
      }),
    ),
    /invalid_event_stream/,
  );
});
