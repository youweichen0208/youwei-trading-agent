import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ResearchSources,
  canonicalUrl,
  sourceCapabilities,
} from "../../packages/server/src/research-sources.js";

const json = (value: unknown) =>
  new Response(JSON.stringify(value), {
    headers: { "content-type": "application/json" },
  });
test("missing source configuration is explicit and never fabricates empty successful evidence", async () => {
  let calls = 0;
  const sources = new ResearchSources({}, async () => {
    calls++;
    throw new Error("unexpected");
  });
  const result = await sources.collect({ symbol: "NVDA" });
  assert.equal(result.length, 5);
  assert.ok(
    result.every((row) => row.status === "unavailable" && row.failures.length),
  );
  assert.equal(calls, 0);
  assert.ok(sourceCapabilities({}).every((item) => !item.available));
});

test("news stores original body and independent publication/observation times with pagination limits", async () => {
  const sources = new ResearchSources(
    { ALPACA_DATA_KEY: "test", ALPACA_DATA_SECRET: "test" },
    async (input, init) => {
      const url = new URL(String(input));
      assert.equal(url.pathname, "/v1beta1/news");
      assert.equal(url.searchParams.get("symbols"), "NVDA");
      assert.equal(
        (init?.headers as Record<string, string>)["APCA-API-KEY-ID"],
        "test",
      );
      return json({
        news: [
          {
            id: 42,
            created_at: "2025-01-02T18:00:00Z",
            headline: "Earnings released",
            summary: "Reported revenue",
            content: "<p>Revenue &amp; outlook</p>",
            url: "https://example.com/a?utm_source=x",
          },
        ],
        next_page_token: "more",
      });
    },
  );
  const [result] = await sources.collect({
    symbol: "NVDA",
    sources: ["market_news"],
  });
  assert.equal(result!.status, "partial");
  assert.equal(result!.documents[0]!.publishedAt, "2025-01-02T18:00:00.000Z");
  assert.ok(
    Date.parse(result!.documents[0]!.observedAt) >
      Date.parse(result!.documents[0]!.publishedAt!),
  );
  assert.equal(result!.documents[0]!.payload.text, "Revenue & outlook");
  assert.equal(
    result!.documents[0]!.payload.eventKey,
    "url:https://example.com/a",
  );
});

test("direct public HTML works without search credentials and does not claim rendered comments", async () => {
  const sources = new ResearchSources(
    {},
    async () =>
      new Response(
        `<html><title>Company meeting</title><meta property="article:published_time" content="2025-01-02T18:00:00Z"><script>fake price 9999</script><p>${"Meeting questions and management answers. ".repeat(10)}</p></html>`,
        { headers: { "content-type": "text/html" } },
      ),
  );
  const [result] = await sources.collect({
    symbol: "NVDA",
    sources: ["web"],
    urls: ["https://example.com/ir#meeting"],
  });
  assert.equal(result!.status, "partial");
  assert.equal(result!.documents.length, 1);
  assert.equal(result!.documents[0]!.payload.renderedCommentsAvailable, false);
  assert.doesNotMatch(String(result!.documents[0]!.payload.text), /fake price/);
  assert.equal(result!.documents[0]!.payload.url, "https://example.com/ir");
  assert.equal(
    canonicalUrl("https://example.com/a?id=1&utm_campaign=two#c"),
    "https://example.com/a?id=1",
  );
});

test("X keeps authors and observed metrics, reports truncated coverage and service errors", async () => {
  let deny = false;
  const sources = new ResearchSources(
    { X_BEARER_TOKEN: "test" },
    async (input) => {
      const url = new URL(String(input));
      assert.equal(url.pathname, "/2/tweets/search/recent");
      assert.match(url.searchParams.get("query")!, /\$NVDA/);
      return deny
        ? new Response("denied", { status: 403 })
        : json({
            data: [
              {
                id: "42",
                text: "My hypothesis",
                author_id: "7",
                created_at: "2025-01-02T18:00:00Z",
                public_metrics: { like_count: 3 },
              },
            ],
            includes: { users: [{ id: "7", username: "analyst" }] },
            meta: { next_token: "more" },
          });
    },
  );
  const [result] = await sources.collect({ symbol: "NVDA", sources: ["x"] });
  assert.equal(result!.status, "partial");
  assert.equal(result!.documents[0]!.payload.kind, "opinion");
  assert.equal(
    (result!.documents[0]!.payload.author as any).username,
    "analyst",
  );
  deny = true;
  const [error] = await sources.collect({ symbol: "NVDA", sources: ["x"] });
  assert.equal(error!.status, "unavailable");
  assert.deepEqual(error!.failures, ["source_http_403"]);
});

test("macro retrieval fixes the requested vintage instead of using today's revisions", async () => {
  const seen: string[] = [];
  const sources = new ResearchSources(
    { FRED_API_KEY: "test" },
    async (input) => {
      const url = new URL(String(input));
      assert.equal(url.searchParams.get("realtime_start"), "2025-01-02");
      assert.equal(url.searchParams.get("realtime_end"), "2025-01-02");
      seen.push(url.searchParams.get("series_id")!);
      return json({
        observations: [
          {
            date: "2024-12-01",
            value: "100",
            realtime_start: "2025-01-02",
            realtime_end: "2025-01-02",
          },
        ],
      });
    },
  );
  const [result] = await sources.collect({
    symbol: "NVDA",
    sources: ["macro"],
    end: "2025-01-02",
  });
  assert.equal(result!.status, "available");
  assert.equal(seen.length, 6);
  assert.equal(result!.documents[0]!.publishedAt, null);
  assert.equal(result!.documents[0]!.payload.vintageDate, "2025-01-02");
});

test("SEC records company identity, original filings and bounded standard financial facts", async () => {
  const sources = new ResearchSources(
    { SEC_USER_AGENT: "Research contact@example.invalid" },
    async (input) => {
      const path = new URL(String(input)).pathname;
      if (path.includes("company_tickers"))
        return json({ "0": { ticker: "NVDA", cik_str: 1045810 } });
      if (path.includes("submissions"))
        return json({
          name: "NVIDIA",
          sicDescription: "Semiconductors",
          filings: {
            recent: {
              accessionNumber: ["0001-25-001", "0001-25-002"],
              form: ["10-Q", "8-K"],
              filingDate: ["2025-01-02", "2025-03-01"],
              acceptanceDateTime: [
                "2025-01-02T18:00:00Z",
                "2025-03-01T18:00:00Z",
              ],
              primaryDocument: ["report.htm", "later.htm"],
            },
          },
        });
      if (path.includes("companyfacts"))
        return json({
          facts: {
            "us-gaap": {
              NetIncomeLoss: {
                label: "Net income",
                units: {
                  USD: [
                    { filed: "2025-01-02", val: 10 },
                    { filed: "2025-03-01", val: 999 },
                  ],
                },
              },
            },
          },
        });
      assert.match(path, /report.htm$/);
      return new Response("<p>Original filing</p>");
    },
  );
  const [result] = await sources.collect({
    symbol: "NVDA",
    sources: ["sec"],
    start: "2025-01-01",
    end: "2025-01-31",
  });
  assert.equal(result!.status, "available");
  assert.equal(result!.documents.length, 3);
  const filing = result!.documents.find(
    (doc) => doc.payload.kind === "filing",
  )!;
  assert.equal(filing.payload.text, "Original filing");
  const facts = result!.documents.find(
    (doc) => doc.payload.kind === "financial_facts",
  )!;
  assert.deepEqual((facts.payload.facts as any).NetIncomeLoss.units.USD, [
    { filed: "2025-01-02", val: 10 },
  ]);
});

test("one collection deadline cancels pending fetches and streaming bodies while returning completed evidence", async () => {
  let liveFetches = 0,
    cancelledBodies = 0;
  const sources = new ResearchSources(
    { X_BEARER_TOKEN: "test" },
    async (input, init) => {
      const url = String(input);
      if (url.includes("example.com/first"))
        return new Response(`<p>${"First observed document. ".repeat(8)}</p>`, {
          headers: { "content-type": "text/html" },
        });
      if (url.includes("example.com/slow"))
        return new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(
                new TextEncoder().encode("<p>Waiting for the rest"),
              );
            },
            cancel() {
              cancelledBodies++;
            },
          }),
          { headers: { "content-type": "text/html" } },
        );
      liveFetches++;
      return new Promise<Response>((_resolve, reject) => {
        const signal = init!.signal!;
        const stop = () => {
          liveFetches--;
          reject(signal.reason);
        };
        if (signal.aborted) stop();
        else signal.addEventListener("abort", stop, { once: true });
      });
    },
    undefined,
    35,
  );
  const started = Date.now();
  const result = await sources.collect({
    symbol: "NVDA",
    sources: ["web", "x"],
    urls: ["https://example.com/first", "https://example.com/slow"],
  });
  assert.ok(Date.now() - started < 1000);
  assert.equal(result[0]!.status, "partial");
  assert.equal(result[0]!.documents.length, 1);
  assert.equal(result[1]!.status, "unavailable");
  assert.equal(liveFetches, 0);
  assert.equal(cancelledBodies, 1);
});
