import { createHash } from "node:crypto";
import { z } from "zod";
import { DomainError } from "./tasks.js";
import { symbol } from "./business.js";

export const sourceNames = ["market_news", "sec", "web", "x", "macro"] as const;
export type SourceName = (typeof sourceNames)[number];
export const collectEvidenceInput = z
  .object({
    symbol,
    sources: z.array(z.enum(sourceNames)).min(1).max(5).optional(),
    start: z.iso.date().optional(),
    end: z.iso.date().optional(),
    urls: z.array(z.url()).max(8).optional(),
  })
  .strict()
  .refine((v) => !v.start || !v.end || v.start <= v.end, "invalid_date_range");
export type CollectInput = z.infer<typeof collectEvidenceInput>;
export interface EvidenceDocument {
  source: SourceName;
  sourceKey: string;
  publishedAt: string | null;
  observedAt: string;
  payload: Record<string, unknown>;
}
export interface SourceResult {
  source: SourceName;
  status: "available" | "empty" | "partial" | "unavailable";
  documents: EvidenceDocument[];
  failures: string[];
}
export type SourceEnvironment = Record<string, string | undefined>;
export function sourceCapabilities(env: SourceEnvironment = process.env) {
  return sourceNames.map((source) => {
    const requirements: Record<SourceName, string[]> = {
      market_news: ["ALPACA_DATA_KEY", "ALPACA_DATA_SECRET"],
      sec: ["SEC_USER_AGENT"],
      web: ["BRAVE_SEARCH_API_KEY"],
      x: ["X_BEARER_TOKEN"],
      macro: ["FRED_API_KEY"],
    };
    const missing = requirements[source].filter((key) => !env[key]);
    return {
      source,
      available: missing.length === 0,
      missing,
      ...(source === "web"
        ? {
            directUrlsAvailable: true,
            limitation:
              "Public HTML only; script-rendered comments are not fetched",
          }
        : {}),
      ...(source === "x"
        ? {
            limitation:
              "Recent search only (last seven days), subject to account access",
          }
        : {}),
    };
  });
}
export function htmlText(html: string, maxLength = 60_000) {
  return html
    .replace(/<(script|style|noscript)\b[^>]*>[\s\S]*?<\/\1>/gi, " ")
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maxLength);
}
function timestamp(value: unknown): string | null {
  if (typeof value !== "string" || !/T.*(?:Z|[+-]\d\d:\d\d)$/.test(value))
    return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed).toISOString() : null;
}
export function canonicalUrl(raw: string) {
  const url = new URL(raw);
  if (
    !["https:", "http:"].includes(url.protocol) ||
    url.username ||
    url.password
  )
    throw new DomainError("invalid_source_url", 400);
  url.hash = "";
  for (const key of [...url.searchParams.keys()])
    if (/^(utm_|fbclid$|gclid$)/i.test(key)) url.searchParams.delete(key);
  return url.toString();
}
export async function responseText(
  response: Response,
  maxBytes = 8 * 1024 * 1024,
  signal?: AbortSignal,
) {
  if (!response.ok) {
    await response.body?.cancel();
    throw new DomainError(`source_http_${response.status}`, 503);
  }
  const reader = response.body?.getReader();
  if (!reader) throw new DomainError("source_empty_response", 503);
  let size = 0;
  const chunks: Uint8Array[] = [];
  const abort = () => {
    void reader.cancel().catch(() => {});
  };
  signal?.addEventListener("abort", abort, { once: true });
  try {
    for (;;) {
      signal?.throwIfAborted();
      const item = await reader.read();
      signal?.throwIfAborted();
      if (item.done) break;
      size += item.value.length;
      if (size > maxBytes)
        throw new DomainError("source_response_too_large", 503);
      chunks.push(item.value);
    }
    return Buffer.concat(chunks).toString("utf8");
  } finally {
    signal?.removeEventListener("abort", abort);
    await reader.cancel();
    reader.releaseLock();
  }
}
export function sourceError(error: unknown) {
  return error instanceof DomainError
    ? error.message
    : error instanceof Error && error.name === "TimeoutError"
      ? "source_timeout"
      : "source_unavailable";
}
export class ResearchSources {
  private tickers?: Promise<Record<string, any>>;
  constructor(
    readonly env: SourceEnvironment = process.env,
    readonly transport: typeof fetch = fetch,
    private readonly signal?: AbortSignal,
    private readonly budgetMs = 22_000,
  ) {}
  private async text(
    url: string,
    headers: Record<string, string> = {},
    maxBytes?: number,
  ) {
    const signal = AbortSignal.any([
      AbortSignal.timeout(15_000),
      ...(this.signal ? [this.signal] : []),
    ]);
    return responseText(
      await this.transport(url, { headers, signal }),
      maxBytes,
      signal,
    );
  }
  private async json(
    url: string,
    headers: Record<string, string> = {},
    maxBytes?: number,
  ): Promise<any> {
    return JSON.parse(await this.text(url, headers, maxBytes));
  }
  async collect(input: CollectInput): Promise<SourceResult[]> {
    const request = collectEvidenceInput.parse(input);
    // One independent deadline per collection; no shared mutable cancellation state across owners.
    const controller = new AbortController();
    const timer = setTimeout(
      () =>
        controller.abort(
          new DOMException("Collection deadline exceeded", "TimeoutError"),
        ),
      Math.min(this.budgetMs, 22_000),
    );
    const run = new ResearchSources(
      this.env,
      this.transport,
      controller.signal,
    );
    try {
      return await run.collectWithinDeadline(request);
    } finally {
      clearTimeout(timer);
    }
  }
  private async collectWithinDeadline(
    request: CollectInput,
  ): Promise<SourceResult[]> {
    const settled = await Promise.allSettled(
      (request.sources ?? [...sourceNames]).map(async (source) => {
        const capability = sourceCapabilities(this.env).find(
          (item) => item.source === source,
        )!;
        if (
          !capability.available &&
          !(source === "web" && request.urls?.length)
        )
          return {
            source,
            status: "unavailable" as const,
            documents: [],
            failures: [`missing_configuration:${capability.missing.join(",")}`],
          };
        return this[source](request);
      }),
    );
    return settled.map((result, index) =>
      result.status === "fulfilled"
        ? result.value
        : {
            source: (request.sources ?? sourceNames)[index]!,
            status: "unavailable",
            documents: [],
            failures: [sourceError(result.reason)],
          },
    );
  }
  private document(
    source: SourceName,
    sourceKey: string,
    publishedAt: unknown,
    payload: Record<string, unknown>,
  ): EvidenceDocument {
    let eventKey = `${source}:${sourceKey}`;
    if (typeof payload.url === "string") {
      try {
        eventKey = `url:${canonicalUrl(payload.url)}`;
      } catch {
        /* Preserve provider identity if it has no usable URL. */
      }
    }
    return {
      source,
      sourceKey,
      publishedAt: timestamp(publishedAt),
      observedAt: new Date().toISOString(),
      payload: { ...payload, eventKey },
    };
  }
  private result(
    source: SourceName,
    documents: EvidenceDocument[],
    failures: string[] = [],
  ): SourceResult {
    return {
      source,
      documents,
      failures,
      status: failures.length
        ? documents.length
          ? "partial"
          : "unavailable"
        : documents.length
          ? "available"
          : "empty",
    };
  }
  async market_news(input: CollectInput) {
    const url = new URL("https://data.alpaca.markets/v1beta1/news");
    url.searchParams.set("symbols", input.symbol);
    url.searchParams.set("limit", "50");
    url.searchParams.set("include_content", "true");
    if (input.start) url.searchParams.set("start", `${input.start}T00:00:00Z`);
    if (input.end) url.searchParams.set("end", `${input.end}T23:59:59Z`);
    const response = await this.json(url.toString(), {
      "APCA-API-KEY-ID": this.env.ALPACA_DATA_KEY!,
      "APCA-API-SECRET-KEY": this.env.ALPACA_DATA_SECRET!,
    });
    if (!Array.isArray(response.news))
      throw new DomainError("invalid_news_response", 503);
    const documents = response.news.map((item: any) =>
      this.document("market_news", String(item.id), item.created_at, {
        kind: "news",
        title: item.headline,
        summary: item.summary,
        text: htmlText(item.content ?? ""),
        url: item.url,
        author: item.author,
        publisher: item.source,
        symbols: item.symbols,
        updatedAt: item.updated_at,
      }),
    );
    return this.result(
      "market_news",
      documents,
      response.next_page_token ? ["news_window_truncated_at_50"] : [],
    );
  }
  async sec(input: CollectInput) {
    const headers = {
      "User-Agent": this.env.SEC_USER_AGENT!,
      "Accept-Encoding": "gzip, deflate",
      Accept: "application/json",
    };
    this.tickers ??= this.json(
      "https://www.sec.gov/files/company_tickers.json",
      headers,
    ).catch((error) => {
      this.tickers = undefined;
      throw error;
    });
    const company = Object.values(await this.tickers).find(
      (item) =>
        item.ticker === input.symbol ||
        item.ticker === input.symbol.replace(".", "-"),
    );
    if (!company) return this.result("sec", [], ["sec_company_not_found"]);
    const cik = String(company.cik_str).padStart(10, "0");
    const url = `https://data.sec.gov/submissions/CIK${cik}.json`;
    const submission = await this.json(url, headers);
    const recent = submission.filings?.recent;
    if (!recent || !Array.isArray(recent.accessionNumber))
      throw new DomainError("invalid_sec_submissions", 503);
    const documents: EvidenceDocument[] = [
      this.document("sec", `company:${cik}`, null, {
        kind: "company",
        cik,
        name: submission.name,
        sic: submission.sic,
        industry: submission.sicDescription,
        exchanges: submission.exchanges,
        tickers: submission.tickers,
        fiscalYearEnd: submission.fiscalYearEnd,
        url,
      }),
    ];
    const failures: string[] = [];
    const indices = recent.accessionNumber
      .map((_: unknown, i: number) => i)
      .filter(
        (i: number) =>
          /^(10-K|10-Q|8-K|20-F|6-K|DEF 14A|4|13F-HR)(\/A)?$/.test(
            recent.form[i],
          ) &&
          (!input.start || recent.filingDate[i] >= input.start) &&
          (!input.end || recent.filingDate[i] <= input.end),
      )
      .slice(0, 12);
    for (const i of indices) {
      if (this.signal?.aborted) {
        failures.push("collection_deadline");
        break;
      }
      const accession = recent.accessionNumber[i];
      const filingUrl = `https://www.sec.gov/Archives/edgar/data/${Number(cik)}/${accession.replaceAll("-", "")}/${recent.primaryDocument[i]}`;
      let text: string | undefined;
      if (documents.length <= 5) {
        try {
          text = htmlText(await this.text(filingUrl, headers));
        } catch (error) {
          failures.push(`filing:${accession}:${sourceError(error)}`);
        }
      }
      documents.push(
        this.document("sec", accession, recent.acceptanceDateTime?.[i], {
          kind: "filing",
          accession,
          form: recent.form[i],
          filingDate: recent.filingDate[i],
          reportDate: recent.reportDate?.[i],
          title: `${submission.name} ${recent.form[i]} ${recent.filingDate[i]}`,
          url: filingUrl,
          ...(text ? { text, textLimit: 60000 } : { textUnavailable: true }),
        }),
      );
    }
    try {
      this.signal?.throwIfAborted();
      const factsUrl = `https://data.sec.gov/api/xbrl/companyfacts/CIK${cik}.json`;
      const facts = await this.json(factsUrl, headers, 24 * 1024 * 1024);
      const selected: Record<string, unknown> = {};
      for (const tag of [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "NetIncomeLoss",
        "EarningsPerShareDiluted",
        "GrossProfit",
        "OperatingIncomeLoss",
        "NetCashProvidedByUsedInOperatingActivities",
        "Assets",
        "StockholdersEquity",
        "LongTermDebtCurrent",
        "LongTermDebtNoncurrent",
      ]) {
        const fact = facts.facts?.["us-gaap"]?.[tag];
        if (!fact) continue;
        selected[tag] = {
          label: fact.label,
          units: Object.fromEntries(
            Object.entries(fact.units ?? {}).map(([unit, rows]) => [
              unit,
              (rows as any[])
                .filter((row) => !input.end || row.filed <= input.end)
                .slice(-12),
            ]),
          ),
        };
      }
      documents.push(
        this.document("sec", `facts:${cik}`, null, {
          kind: "financial_facts",
          url: factsUrl,
          facts: selected,
          pointInTimeVerified: false,
        }),
      );
    } catch (error) {
      failures.push(`companyfacts:${sourceError(error)}`);
    }
    return this.result("sec", documents, failures);
  }
  async web(input: CollectInput) {
    const urls = new Map<string, { title?: string; snippet?: string }>();
    for (const value of input.urls ?? []) urls.set(canonicalUrl(value), {});
    const failures: string[] = [];
    if (this.env.BRAVE_SEARCH_API_KEY) {
      const query = new URL("https://api.search.brave.com/res/v1/web/search");
      query.searchParams.set(
        "q",
        `${input.symbol} stock earnings investor relations outlook discussion`,
      );
      query.searchParams.set("count", "8");
      if (input.start && input.end)
        query.searchParams.set("freshness", `${input.start}to${input.end}`);
      try {
        const data = await this.json(query.toString(), {
          "X-Subscription-Token": this.env.BRAVE_SEARCH_API_KEY,
        });
        if (!Array.isArray(data.web?.results))
          failures.push("search_no_results");
        for (const item of data.web?.results ?? []) {
          if (typeof item.url === "string")
            urls.set(canonicalUrl(item.url), {
              title: item.title,
              snippet: item.description,
            });
        }
      } catch (error) {
        failures.push(`search:${sourceError(error)}`);
      }
    } else if (input.urls?.length)
      failures.push("web_search_unconfigured_direct_urls_only");
    const documents: EvidenceDocument[] = [];
    for (const [url, metadata] of [...urls.entries()].slice(0, 8)) {
      if (this.signal?.aborted) {
        failures.push("collection_deadline");
        break;
      }
      try {
        const signal = AbortSignal.any([
          AbortSignal.timeout(15_000),
          ...(this.signal ? [this.signal] : []),
        ]);
        const response = await this.transport(url, {
          signal,
          headers: { Accept: "text/html,text/plain" },
        });
        const contentType = response.headers.get("content-type") ?? "";
        if (!/text\/(html|plain)|application\/xhtml/.test(contentType)) {
          await response.body?.cancel();
          failures.push(`unsupported_page_format:${url}`);
          continue;
        }
        const html = await responseText(response, 2 * 1024 * 1024, signal);
        const text = htmlText(html);
        if (text.length < 80) {
          failures.push(`page_content_unavailable:${url}`);
          continue;
        }
        const meta = html.match(
          /<meta\b[^>]*(?:property|name)=["'](?:article:published_time|datePublished)["'][^>]*content=["']([^"']+)/i,
        )?.[1];
        const jsonDate = html.match(/"datePublished"\s*:\s*"([^"']+)"/i)?.[1];
        const title =
          metadata.title ??
          htmlText(html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1] ?? "");
        documents.push(
          this.document("web", url, meta ?? jsonDate, {
            kind: "web_page",
            url,
            title,
            text,
            searchSnippet: metadata.snippet,
            retrieval: "public_html",
            renderedCommentsAvailable: false,
            textLimit: 60000,
            contentHash: createHash("sha256").update(text).digest("hex"),
          }),
        );
      } catch (error) {
        failures.push(`page:${url}:${sourceError(error)}`);
      }
    }
    return this.result("web", documents, failures);
  }
  async x(input: CollectInput) {
    const url = new URL("https://api.x.com/2/tweets/search/recent");
    url.searchParams.set(
      "query",
      `($${input.symbol} OR ${input.symbol}) -is:retweet lang:en`,
    );
    url.searchParams.set("max_results", "50");
    url.searchParams.set(
      "tweet.fields",
      "created_at,author_id,conversation_id,referenced_tweets,public_metrics",
    );
    url.searchParams.set("expansions", "author_id");
    url.searchParams.set("user.fields", "username,name,description");
    const oldest = new Date(Date.now() - 6 * 86400_000).toISOString();
    url.searchParams.set(
      "start_time",
      input.start && `${input.start}T00:00:00Z` > oldest
        ? `${input.start}T00:00:00Z`
        : oldest,
    );
    if (input.end && `${input.end}T23:59:59Z` < new Date().toISOString())
      url.searchParams.set("end_time", `${input.end}T23:59:59Z`);
    const result = await this.json(url.toString(), {
      Authorization: `Bearer ${this.env.X_BEARER_TOKEN}`,
    });
    if (!result.meta && !result.data)
      throw new DomainError("invalid_x_response", 503);
    const documents = (result.data ?? []).map((item: any) => {
      const author = result.includes?.users?.find(
        (user: any) => user.id === item.author_id,
      );
      return this.document("x", item.id, item.created_at, {
        kind: "opinion",
        text: item.text,
        author,
        authorId: item.author_id,
        conversationId: item.conversation_id,
        references: item.referenced_tweets,
        metricsAtObservation: item.public_metrics,
        url: `https://x.com/i/status/${item.id}`,
      });
    });
    const failures = result.errors?.length ? ["x_partial_response"] : [];
    if (result.meta?.next_token) failures.push("x_window_truncated_at_50");
    return this.result("x", documents, failures);
  }
  async macro(input: CollectInput) {
    const documents: EvidenceDocument[] = [],
      failures: string[] = [];
    const asOf = input.end ?? new Date().toISOString().slice(0, 10);
    for (const series of [
      "DFF",
      "DGS10",
      "T10Y2Y",
      "CPIAUCSL",
      "UNRATE",
      "VIXCLS",
    ]) {
      if (this.signal?.aborted) {
        failures.push("collection_deadline");
        break;
      }
      try {
        const url = new URL(
          "https://api.stlouisfed.org/fred/series/observations",
        );
        for (const [key, value] of Object.entries({
          series_id: series,
          api_key: this.env.FRED_API_KEY!,
          file_type: "json",
          realtime_start: asOf,
          realtime_end: asOf,
          sort_order: "desc",
          limit: "12",
        }))
          url.searchParams.set(key, value);
        const result = await this.json(url.toString());
        if (!Array.isArray(result.observations))
          throw new DomainError("invalid_macro_response", 503);
        if (!result.observations.length) {
          failures.push(`macro_no_observations:${series}`);
          continue;
        }
        documents.push(
          this.document("macro", `${series}:${asOf}`, null, {
            kind: "macro_series",
            series,
            vintageDate: asOf,
            observations: result.observations,
            url: `https://fred.stlouisfed.org/series/${series}`,
            timestampPrecision: "date",
          }),
        );
      } catch (error) {
        failures.push(`macro:${series}:${sourceError(error)}`);
      }
    }
    return this.result("macro", documents, failures);
  }
}
