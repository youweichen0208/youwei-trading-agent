import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readRunEvents } from "../../packages/hermes-adapter/src/events.js";

const frame = (seq: number, event: string, data: object = {}) =>
  `id: ${seq}\r\ndata: ${JSON.stringify({ run_id: "run-test", seq, event, ...data })}\r\n\r\n`;
async function sample(body: string, after = -1) {
  return readRunEvents(
    async () =>
      new Response(body, {
        headers: { "Content-Type": "text/event-stream" },
      }),
    "http://unused",
    "private-api-key",
    "run-test",
    after,
  );
}
test("native SSE identity, replay gaps and bounded safe event mapping", async () => {
  const batch = await sample(
    frame(2, "message.delta", { delta: "你好 private-api-key" }) +
      frame(3, "tool.completed", {
        tool: "market_history",
        preview: "SECRET",
        args: { price: 42 },
      }) +
      frame(4, "reasoning.available", { text: "private reasoning" }) +
      ": stream closed\r\n\r\n",
    1,
  );
  assert.deepEqual(batch, {
    gap: false,
    closed: true,
    limited: false,
    events: [
      { sequence: 2, kind: "message.delta", text: "你好 [redacted]" },
      { sequence: 3, kind: "tool.completed", tool: "market_history" },
      { sequence: 4, kind: "reasoning.available" },
    ],
  });
  assert.equal(
    (await sample(frame(5, "message.delta", { delta: "gap" }))).gap,
    true,
  );
  assert.equal(
    (await sample('data: {"run_id":"run-test","event":"replay.truncated"}\n\n'))
      .gap,
    true,
  );
  assert.equal((await sample(frame(2, "run.completed"), 2)).events.length, 0);
  await assert.rejects(
    sample(frame(0, "tool.started").replace("run-test", "foreign")),
    /identity_mismatch/,
  );
  await assert.rejects(
    sample(frame(0, "tool.started").replace("id: 0", "id: 1")),
    /cursor_invalid/,
  );
  await assert.rejects(
    sample("data: " + "x".repeat(70_000)),
    /frame_too_large/,
  );
  await assert.rejects(sample("x".repeat(1_048_577)), /response_too_large/);
  const limited = await sample(
    Array.from({ length: 300 }, (_, i) => frame(i, "run.running")).join(""),
  );
  assert.equal(limited.events.length, 256);
  assert.equal(limited.limited, true);
});
test("bounded real HTTP sample preserves Unicode framing and reconnect cursor", async () => {
  const server = createServer((req, res) => {
    assert.equal(req.headers.authorization, "Bearer key");
    assert.equal(req.headers["last-event-id"], "0");
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    const bytes = Buffer.from(frame(1, "message.delta", { delta: "中文" }));
    const split = bytes.indexOf(Buffer.from("中文")) + 1;
    res.write(bytes.subarray(0, split));
    res.write(bytes.subarray(split));
    res.write('id: 2\ndata: {"partial":'); // deadline cuts an incomplete frame
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  try {
    const batch = await readRunEvents(
      fetch,
      `http://127.0.0.1:${address.port}`,
      "key",
      "run-test",
      0,
      200,
    );
    assert.deepEqual(batch.events, [
      { sequence: 1, kind: "message.delta", text: "中文" },
    ]);
    assert.equal(batch.closed, false);
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
