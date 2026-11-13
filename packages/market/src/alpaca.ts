import { z } from "zod";
import { DomainError } from "../../server/src/tasks.js";

export interface MarketQuery {
  symbol: string;
  start: string;
  end: string;
  adjustment: "raw" | "split";
}
export interface MarketProvider {
  readonly source: string;
  readonly version: string;
  readonly feed: string;
  bars(query: MarketQuery, signal: AbortSignal): Promise<unknown[]>;
}
const bar = z.object({
  t: z.iso.datetime({ offset: true }),
  o: z.string(),
  h: z.string(),
  l: z.string(),
  c: z.string(),
  v: z.string(),
});
const page = z.object({
  bars: z.record(z.string(), z.array(bar)).nullable(),
  next_page_token: z.string().max(4096).nullish(),
});

export function parseFinancialJSON(text: string): unknown {
  return JSON.parse(
    text,
    (_key: string, value: unknown, context?: { source: string }) => {
      if (typeof value !== "number") return value;
      if (!context?.source)
        throw new DomainError("lossless_json_unsupported", 503);
      return context.source;
    },
  );
}
async function body(response: Response) {
  if (!response.ok) {
    await response.body?.cancel();
    throw new DomainError(`market_http_${response.status}`, 503);
  }
  const reader = response.body?.getReader();
  if (!reader) throw new DomainError("market_empty_response", 503);
  let size = 0;
  const chunks: Uint8Array[] = [];
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > 4 * 1024 * 1024)
        throw new DomainError("market_response_too_large", 503);
      chunks.push(value);
    }
    return parseFinancialJSON(Buffer.concat(chunks).toString("utf8"));
  } finally {
    await reader.cancel();
    reader.releaseLock();
  }
}
export class AlpacaProvider implements MarketProvider {
  readonly source = "alpaca";
  readonly version = "alpaca-v2-bars-1day-v1";
  constructor(
    private key: string,
    private secret: string,
    readonly feed: "iex" | "sip",
    private transport: typeof fetch = fetch,
  ) {}
  async latestTrade(symbol: string, signal: AbortSignal) {
    if (!/^[A-Z][A-Z0-9.-]{0,9}$/.test(symbol))
      throw new DomainError("invalid_symbol", 422);
    const url = new URL(
      `https://data.alpaca.markets/v2/stocks/${encodeURIComponent(symbol)}/trades/latest`,
    );
    url.searchParams.set("feed", this.feed);
    const result = z
      .object({
        symbol: z.literal(symbol),
        trade: z.object({
          p: z.string().regex(/^\d+(?:\.\d+)?$/),
          t: z.iso.datetime({ offset: true }),
        }),
      })
      .parse(
        await body(
          await this.transport(url, {
            signal: AbortSignal.any([signal, AbortSignal.timeout(6000)]),
            redirect: "error",
            headers: {
              "APCA-API-KEY-ID": this.key,
              "APCA-API-SECRET-KEY": this.secret,
            },
          }),
        ),
      );
    return {
      symbol,
      price: result.trade.p,
      time: result.trade.t,
      source: this.source,
      feed: this.feed,
    };
  }
  async bars(query: MarketQuery, signal: AbortSignal) {
    const rows: unknown[] = [];
    const tokens = new Set<string>();
    let token: string | undefined;
    const combined = AbortSignal.any([signal, AbortSignal.timeout(15_000)]);
    for (let count = 0; count < 20; count++) {
      const url = new URL("https://data.alpaca.markets/v2/stocks/bars");
      for (const [k, v] of Object.entries({
        symbols: query.symbol,
        timeframe: "1Day",
        start: query.start,
        end: `${query.end}T23:59:59Z`,
        adjustment: query.adjustment,
        feed: this.feed,
        currency: "USD",
        limit: "10000",
        sort: "asc",
        asof: "-",
        ...(token ? { page_token: token } : {}),
      }))
        url.searchParams.set(k, v);
      const result = page.parse(
        await body(
          await this.transport(url, {
            signal: combined,
            redirect: "error",
            headers: {
              "APCA-API-KEY-ID": this.key,
              "APCA-API-SECRET-KEY": this.secret,
            },
          }),
        ),
      );
      if (
        result.bars &&
        Object.keys(result.bars).some((key) => key !== query.symbol)
      )
        throw new DomainError("market_symbol_mismatch", 503);
      for (const item of result.bars?.[query.symbol] ?? []) {
        const date = new Date(item.t);
        const parts = new Intl.DateTimeFormat("en-CA", {
          timeZone: "America/New_York",
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
        }).format(date);
        rows.push({
          session: parts,
          open: item.o,
          high: item.h,
          low: item.l,
          close: item.c,
          volume: item.v,
        });
      }
      if (rows.length > 2600) throw new DomainError("market_row_limit", 422);
      if (!result.next_page_token) return rows;
      if (tokens.has(result.next_page_token))
        throw new DomainError("market_pagination_cycle", 503);
      token = result.next_page_token;
      tokens.add(token);
    }
    throw new DomainError("market_page_limit", 503);
  }
}
export function configuredMarketProvider(): MarketProvider | undefined {
  if (!process.env.MARKET_PROVIDER) return undefined;
  if (process.env.MARKET_PROVIDER !== "alpaca")
    throw new Error("unsupported_market_provider");
  const key = process.env.ALPACA_DATA_KEY,
    secret = process.env.ALPACA_DATA_SECRET,
    feed = process.env.ALPACA_DATA_FEED;
  if (!key || !secret || !["iex", "sip"].includes(feed ?? ""))
    throw new Error("market_configuration_incomplete");
  return new AlpacaProvider(key, secret, feed as "iex" | "sip");
}
