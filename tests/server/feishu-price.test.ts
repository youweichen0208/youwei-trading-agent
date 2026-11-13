import { test } from "node:test";
import assert from "node:assert/strict";
import {
  priceSymbol,
  priceReply,
} from "../../packages/server/src/feishu-price.js";
import { AlpacaProvider } from "../../packages/market/src/alpaca.js";
test("natural stock lookup bypasses research; analytical requests stay intact", () => {
  for (const text of [
    "可以帮我查询一下英伟达的股票吗",
    "英伟达现在股价多少",
    "请查询 NVDA 股价",
    "查一下英伟达",
    "英伟达多少钱",
  ])
    assert.equal(priceSymbol(text), "NVDA", text);
  for (const text of [
    "分析英伟达",
    "查询NVDA历史股价",
    "查看 AAPL 最新预测",
    "查一下苹果和微软",
    "NVDA值得买吗",
  ])
    assert.equal(priceSymbol(text), null, text);
});
test("direct quote validates symbol and reports provenance and time", async () => {
  const provider = new AlpacaProvider("test", "test", "iex", async (url) => {
    assert.ok(String(url).includes("/NVDA/trades/latest?feed=iex"));
    return new Response(
      '{"symbol":"NVDA","trade":{"p":173.85,"t":"2026-11-12T20:59:00Z"}}',
    );
  });
  const reply = await priceReply("可以帮我查询一下英伟达的股票吗", provider);
  assert.ok(
    reply?.includes("173.85 USD") &&
      reply.includes("2026-11-12T20:59:00Z") &&
      reply.includes("IEX"),
  );
  const failed = await priceReply("查NVDA股价", {
    latestTrade: async () => {
      throw Error("unavailable");
    },
  });
  assert.ok(failed?.includes("未启动研究"));
});

test("company names are not silently turned into ticker symbols", async () => {
  let calls = 0;
  const reply = await priceReply("可以帮我查询一下unknownco", {
    latestTrade: async () => {
      calls++;
      throw Error("must not query an invented symbol");
    },
  });
  assert.equal(calls, 0);
  assert.ok(reply?.includes("公司资料") && reply.includes("股票代码"));
  assert.ok(reply !== null && !reply.includes("稍后重试"));
});

test("unavailable symbols are distinguished from temporary provider failures", async () => {
  const provider = new AlpacaProvider(
    "test",
    "test",
    "iex",
    async () => new Response("", { status: 404 }),
  );
  const reply = await priceReply("查 XXXX 股价", provider);
  assert.ok(reply?.includes("没有找到"));
  assert.ok(reply !== null && !reply.includes("稍后重试"));
});

test("SpaceX lookup and existing clarification resolve official ticker SPCX", async () => {
  const symbols: string[] = [];
  const provider: Pick<AlpacaProvider, "latestTrade"> = {
    latestTrade: async (symbol: string) => {
      symbols.push(symbol);
      return {
        symbol,
        price: "100.00",
        time: "2026-11-12T21:00:00Z",
        source: "alpaca",
        feed: "iex",
      };
    },
  };
  for (const input of [
    "可以帮我查询一下spacex",
    "SpaceX股价",
    "查 SPCX 股价",
    "股票行情",
  ]) {
    const reply = await priceReply(input, provider, {
      target: "SPACEX",
      at: new Date().toISOString(),
    });
    assert.ok(
      reply?.includes("SpaceX（SPCX）") && reply.includes("100.00 USD"),
    );
  }
  assert.deepEqual(symbols, ["SPCX", "SPCX", "SPCX", "SPCX"]);
  assert.ok((await priceReply("股票行情", provider))?.includes("哪家"));
  assert.ok(
    (
      await priceReply("股票行情", provider, {
        target: "SPACEX",
        at: "2020-01-01T00:00:00Z",
      })
    )?.includes("哪家"),
  );
});
