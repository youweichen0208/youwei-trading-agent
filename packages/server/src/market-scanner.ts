import { createHash, randomUUID } from "node:crypto";
import type { Pool } from "pg";
import { DomainError } from "./tasks.js";
import { QuantRunner } from "./quant-runner.js";
import {
  responseText,
  sourceError,
  type SourceEnvironment,
} from "./research-sources.js";
import { symbol as symbolSchema } from "./business.js";

export interface ScanAsset {
  symbol: string;
  name: string;
  exchange: string;
}
export interface ScanBar {
  session: string;
  close: number;
  volume: number;
}
export interface ScanObservation {
  symbol: string;
  relativeReturn: number;
  volumeRatio: number;
  announcementCount: number | null;
}
export interface ScanCandidate extends ScanObservation {
  score: number;
  reason: "ranked" | "exploration";
}
export interface ScanProvider {
  readonly feed: string;
  assets(): Promise<ScanAsset[]>;
  bars(
    symbols: string[],
    start: string,
    end: string,
  ): Promise<Record<string, ScanBar[]>>;
  announcements(
    start: string,
    end: string,
  ): Promise<{ counts: Record<string, number>; complete: boolean }>;
}
export function rankCandidates(
  observations: ScanObservation[],
  seed: string,
  exclude: string[] = [],
): ScanCandidate[] {
  const valid = observations.filter(
    (item) =>
      !exclude.includes(item.symbol) &&
      Number.isFinite(item.relativeReturn) &&
      Number.isFinite(item.volumeRatio),
  );
  // Tied observations receive the same midpoint percentile; this avoids symbol-order bias.
  const percentiles = (values: number[]) => {
    const sorted = [...values].sort((a, b) => a - b),
      result = new Map<number, number>();
    for (let i = 0; i < sorted.length;) {
      let end = i + 1;
      while (end < sorted.length && sorted[end] === sorted[i]) end++;
      result.set(
        sorted[i]!,
        sorted.length < 2 ? 0.5 : (i + (end - i - 1) / 2) / (sorted.length - 1),
      );
      i = end;
    }
    return result;
  };
  const returns = percentiles(
    valid.map((item) => Math.abs(item.relativeReturn)),
  );
  const volume = percentiles(valid.map((item) => item.volumeRatio));
  const news = percentiles(
    valid
      .filter((item) => item.announcementCount !== null)
      .map((item) => item.announcementCount!),
  );
  const ranked: ScanCandidate[] = valid
    .map((item) => {
      const factors = [
        returns.get(Math.abs(item.relativeReturn))!,
        volume.get(item.volumeRatio)!,
      ];
      if (item.announcementCount !== null)
        factors.push(news.get(item.announcementCount)!);
      return {
        ...item,
        score: factors.reduce((sum, value) => sum + value, 0) / factors.length,
        reason: "ranked" as const,
      };
    })
    .sort((a, b) => b.score - a.score || a.symbol.localeCompare(b.symbol));
  const top = ranked.slice(0, 8),
    remaining = ranked.slice(8);
  const explored = [...remaining]
    .sort((a, b) => {
      const hash = (ticker: string) =>
        createHash("sha256").update(`${seed}:${ticker}`).digest("hex");
      return hash(a.symbol).localeCompare(hash(b.symbol));
    })
    .slice(0, 2)
    .map((item) => ({ ...item, reason: "exploration" as const }));
  return [
    ...top,
    ...explored,
    ...remaining.filter(
      (item) => !explored.some((pick) => pick.symbol === item.symbol),
    ),
  ];
}

export class AlpacaScanProvider implements ScanProvider {
  readonly feed: string;
  constructor(
    readonly env: SourceEnvironment = process.env,
    readonly transport: typeof fetch = fetch,
  ) {
    this.feed = env.ALPACA_DATA_FEED ?? "";
  }
  private async json(url: string): Promise<any> {
    if (
      !this.env.ALPACA_DATA_KEY ||
      !this.env.ALPACA_DATA_SECRET ||
      !["iex", "sip"].includes(this.feed)
    )
      throw new DomainError("scanner_configuration_incomplete", 503);
    const response = await this.transport(url, {
      signal: AbortSignal.timeout(30_000),
      headers: {
        "APCA-API-KEY-ID": this.env.ALPACA_DATA_KEY,
        "APCA-API-SECRET-KEY": this.env.ALPACA_DATA_SECRET,
      },
    });
    return JSON.parse(await responseText(response, 16 * 1024 * 1024));
  }
  async assets(): Promise<ScanAsset[]> {
    const origin =
      this.env.ALPACA_ASSET_API_ORIGIN ?? "https://paper-api.alpaca.markets";
    if (
      ![
        "https://paper-api.alpaca.markets",
        "https://api.alpaca.markets",
      ].includes(origin)
    )
      throw new DomainError("invalid_asset_api_origin", 400);
    const data = await this.json(
      `${origin}/v2/assets?status=active&asset_class=us_equity`,
    );
    if (!Array.isArray(data))
      throw new DomainError("invalid_assets_response", 503);
    return data
      .filter(
        (item: any) =>
          item.status === "active" &&
          item.class === "us_equity" &&
          item.tradable &&
          ["NYSE", "NASDAQ", "AMEX", "ARCA", "BATS", "NYSEARCA"].includes(
            item.exchange,
          ) &&
          symbolSchema.safeParse(item.symbol).success &&
          !/\b(warrants?|preferred|rights|units|ETF|ETN)\b/i.test(
            item.name ?? "",
          ),
      )
      .map((item: any) => ({
        symbol: item.symbol,
        name: item.name,
        exchange: item.exchange,
      }));
  }
  async bars(symbols: string[], start: string, end: string) {
    const result: Record<string, ScanBar[]> = {};
    const tokens = new Set<string>();
    let token: string | null = null;
    for (let page = 0; page < 20; page++) {
      const url = new URL("https://data.alpaca.markets/v2/stocks/bars");
      for (const [key, value] of Object.entries({
        symbols: symbols.join(","),
        timeframe: "1Day",
        start,
        end: `${end}T23:59:59Z`,
        adjustment: "split",
        feed: this.feed,
        limit: "10000",
        sort: "asc",
        asof: "-",
      }))
        url.searchParams.set(key, value);
      if (token) url.searchParams.set("page_token", token);
      const data = await this.json(url.toString());
      if (
        data.bars !== null &&
        (!data.bars ||
          typeof data.bars !== "object" ||
          Array.isArray(data.bars))
      )
        throw new DomainError("invalid_scan_bars", 503);
      for (const [ticker, bars] of Object.entries(data.bars ?? {})) {
        if (!symbols.includes(ticker) || !Array.isArray(bars))
          throw new DomainError("invalid_scan_bars", 503);
        result[ticker] ??= [];
        for (const bar of bars) {
          const date = new Date(bar.t);
          if (
            !Number.isFinite(date.getTime()) ||
            !Number.isFinite(Number(bar.c)) ||
            Number(bar.c) <= 0 ||
            !Number.isFinite(Number(bar.v)) ||
            Number(bar.v) < 0
          )
            throw new DomainError("invalid_scan_bar", 503);
          result[ticker].push({
            session: new Intl.DateTimeFormat("en-CA", {
              timeZone: "America/New_York",
              year: "numeric",
              month: "2-digit",
              day: "2-digit",
            }).format(date),
            close: Number(bar.c),
            volume: Number(bar.v),
          });
        }
      }
      if (!data.next_page_token) return result;
      if (tokens.has(data.next_page_token))
        throw new DomainError("scan_pagination_cycle", 503);
      token = data.next_page_token;
      tokens.add(token!);
    }
    throw new DomainError("scan_page_limit", 503);
  }
  async announcements(start: string, end: string) {
    const counts: Record<string, number> = {},
      ids = new Set<string>(),
      tokens = new Set<string>();
    let token: string | null = null;
    for (let page = 0; page < 20; page++) {
      const url = new URL("https://data.alpaca.markets/v1beta1/news");
      for (const [key, value] of Object.entries({
        start: `${start}T00:00:00Z`,
        end: `${end}T23:59:59Z`,
        limit: "50",
        sort: "desc",
      }))
        url.searchParams.set(key, value);
      if (token) url.searchParams.set("page_token", token);
      const data = await this.json(url.toString());
      if (!Array.isArray(data.news))
        throw new DomainError("invalid_scan_news", 503);
      for (const item of data.news) {
        if (ids.has(String(item.id))) continue;
        ids.add(String(item.id));
        for (const ticker of item.symbols ?? [])
          counts[ticker] = (counts[ticker] ?? 0) + 1;
      }
      if (!data.next_page_token) return { counts, complete: true };
      if (tokens.has(data.next_page_token))
        throw new DomainError("scan_news_pagination_cycle", 503);
      token = data.next_page_token;
      tokens.add(token!);
    }
    return { counts, complete: false };
  }
}

export class MarketScanner {
  constructor(
    readonly pool: Pool,
    readonly provider: ScanProvider = new AlpacaScanProvider(),
    readonly quant = new QuantRunner(),
  ) {}
  async scan(owner: string, session?: string) {
    const schedule = await this.quant.run({
      operation: "research_schedule",
      asOf: new Date().toISOString(),
    });
    const date = session ?? schedule.latestCompletedSession;
    if (
      !date ||
      !/^\d{4}-\d{2}-\d{2}$/.test(date) ||
      date > schedule.latestCompletedSession
    )
      throw new DomainError("scan_session_not_finalized", 422);
    const dateSchedule =
      date === schedule.latestCompletedSession
        ? schedule
        : await this.quant.run({
            operation: "research_schedule",
            asOf: `${date}T23:59:59Z`,
          });
    if (dateSchedule.latestCompletedSession !== date)
      throw new DomainError("scan_not_trading_session", 422);
    const expectedSessions: string[] = dateSchedule.recentCompletedSessions;
    const previous = (
      await this.pool.query(
        `SELECT id,payload,attempt,created_at FROM market_scan_runs WHERE owner_id=$1 AND session=$2
      ORDER BY (payload->>'status' IN ('completed','partial')) DESC,attempt DESC LIMIT 1`,
        [owner, date],
      )
    ).rows[0];
    if (
      previous &&
      (previous.payload.status !== "unavailable" ||
        Date.now() - new Date(previous.created_at).getTime() < 5 * 60_000)
    )
      return { scanRef: previous.id, ...previous.payload };
    const attempt = previous ? Number(previous.attempt) + 1 : 1;
    const failures: string[] = [],
      missing: string[] = [],
      missingReasons: Record<string, string> = {};
    let assets: ScanAsset[] = [];
    try {
      assets = await this.provider.assets();
      if (!assets.length) failures.push("empty_active_universe");
    } catch (error) {
      failures.push(sourceError(error));
    }
    const benchmarks = [
      "SPY",
      "QQQ",
      "IWM",
      "XLK",
      "XLF",
      "XLE",
      "XLV",
      "XLI",
      "XLY",
      "XLP",
      "XLU",
      "XLB",
      "XLRE",
      "XLC",
    ];
    const symbols = [
      ...new Set([...assets.map((asset) => asset.symbol), ...benchmarks]),
    ];
    const start = new Date(Date.parse(`${date}T00:00:00Z`) - 50 * 86400_000)
      .toISOString()
      .slice(0, 10);
    const allBars: Record<string, ScanBar[]> = {};
    if (assets.length) {
      const batches: string[][] = [];
      for (let i = 0; i < symbols.length; i += 100)
        batches.push(symbols.slice(i, i + 100));
      for (let i = 0; i < batches.length; i += 4) {
        const settled = await Promise.allSettled(
          batches
            .slice(i, i + 4)
            .map((batch) => this.provider.bars(batch, start, date)),
        );
        for (const [offset, result] of settled.entries()) {
          if (result.status === "fulfilled")
            Object.assign(allBars, result.value);
          else {
            failures.push(
              `bars_batch:${i + offset}:${sourceError(result.reason)}`,
            );
            for (const ticker of batches[i + offset]!)
              missingReasons[ticker] = "batch_failed";
          }
        }
      }
    }
    let announcements:
      { counts: Record<string, number>; complete: boolean } | undefined;
    if (assets.length)
      try {
        announcements = await this.provider.announcements(date, date);
        if (!announcements.complete)
          failures.push("announcement_window_truncated");
      } catch (error) {
        failures.push(`announcements:${sourceError(error)}`);
      }
    const benchmark = allBars.SPY?.filter((bar) => bar.session <= date).sort(
      (a, b) => a.session.localeCompare(b.session),
    );
    const latestBenchmark = benchmark?.at(-1),
      priorBenchmark = benchmark?.at(-2);
    const benchmarkReturn =
      latestBenchmark &&
      latestBenchmark.session === date &&
      priorBenchmark &&
      priorBenchmark.session === expectedSessions.at(-2)
        ? latestBenchmark.close / priorBenchmark.close - 1
        : null;
    if (benchmarkReturn === null) failures.push("benchmark_unavailable");
    const observations: ScanObservation[] = [];
    for (const ticker of symbols) {
      const bars = allBars[ticker]
        ?.filter((bar) => bar.session <= date)
        .sort((a, b) => a.session.localeCompare(b.session));
      const last = bars?.at(-1),
        previousBar = bars?.at(-2),
        history = bars?.slice(-21, -1);
      const continuous =
        bars
          ?.slice(-21)
          .map((bar) => bar.session)
          .join(",") === expectedSessions.join(",");
      if (
        !last ||
        last.session !== date ||
        !previousBar ||
        history?.length !== 20 ||
        !continuous ||
        new Set(bars?.map((bar) => bar.session)).size !== bars?.length ||
        !history.some((bar) => bar.volume > 0) ||
        benchmarkReturn === null
      ) {
        missing.push(ticker);
        missingReasons[ticker] ??=
          !last || last.session !== date
            ? "latest_session_missing"
            : "insufficient_or_invalid_history";
        continue;
      }
      const meanVolume =
        history.reduce((total, bar) => total + bar.volume, 0) / history.length;
      observations.push({
        symbol: ticker,
        relativeReturn: last.close / previousBar.close - 1 - benchmarkReturn,
        volumeRatio: last.volume / meanVolume,
        announcementCount: announcements?.complete
          ? (announcements.counts[ticker] ?? 0)
          : null,
      });
    }
    const covered = (
      await this.pool.query(
        "SELECT symbol FROM research_coverage WHERE owner_id=$1 AND tier IN ('holding','watchlist')",
        [owner],
      )
    ).rows.map((row) => row.symbol as string);
    const ranked = rankCandidates(observations, `${owner}:${date}`, [
      ...benchmarks,
      ...covered,
    ]);
    const payload = {
      session: date,
      status: observations.length
        ? failures.length || missing.length
          ? "partial"
          : "completed"
        : "unavailable",
      selected: ranked.slice(0, 50),
      rankings: ranked,
      universe: assets,
      coverage: {
        universe: symbols.length,
        observed: observations.length,
        missing,
      },
      missingReasons,
      failures,
      feed: this.provider.feed,
      benchmark: "SPY",
      benchmarkObservations: observations.filter((row) =>
        benchmarks.includes(row.symbol),
      ),
      scoringVersion: "equal-percentile-relative-return-volume-news-v1",
      announcementSource: "Alpaca news (not comprehensive company filings)",
      universeAsOf: new Date().toISOString(),
      universeLimitations:
        "Current active tradable US-listed equities; instrument exclusion uses asset names. Historical membership is not reconstructed.",
    };
    const id = randomUUID();
    const saved = await this.pool.query(
      "INSERT INTO market_scan_runs(id,owner_id,session,payload,attempt) VALUES($1,$2,$3,$4,$5) ON CONFLICT(owner_id,session,attempt) DO NOTHING RETURNING id",
      [id, owner, date, JSON.stringify(payload), attempt],
    );
    if (!saved.rowCount) {
      const existing = (
        await this.pool.query(
          "SELECT id,payload FROM market_scan_runs WHERE owner_id=$1 AND session=$2 ORDER BY attempt DESC LIMIT 1",
          [owner, date],
        )
      ).rows[0]!;
      return { scanRef: existing.id, ...existing.payload };
    }
    return { scanRef: id, ...payload };
  }
}
