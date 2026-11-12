import { randomUUID } from "node:crypto";
import type { Pool } from "pg";
import { z } from "zod";
import { symbol } from "./business.js";
import { transaction, lockOwner } from "./db.js";
import { DomainError } from "./tasks.js";
import { canonical, sha256 } from "./tool-context.js";
import { QuantRunner } from "./quant-runner.js";
import {
  collectEvidenceInput,
  ResearchSources,
  sourceCapabilities,
  type SourceResult,
} from "./research-sources.js";
export {
  collectEvidenceInput,
  sourceCapabilities,
} from "./research-sources.js";

export const coverageInput = z
  .object({ symbol, action: z.enum(["watch", "pause", "resume", "remove"]) })
  .strict();
export const settingsInput = z
  .object({
    enabled: z.boolean().optional(),
    dailyResearchLimit: z.number().int().min(1).max(100).optional(),
    dailyCandidateLimit: z.number().int().min(0).max(30).optional(),
    digestHour: z.number().int().min(0).max(23).optional(),
    feishuNotifications: z.boolean().optional(),
  })
  .strict();
export const defaultResearchSettings = {
  enabled: true,
  dailyResearchLimit: 30,
  dailyCandidateLimit: 10,
  digestHour: 8,
  feishuNotifications: true,
};
export interface ResearchCoverage {
  owner_id: string;
  symbol: string;
  tier: "holding" | "watchlist" | "temporary" | "candidate";
  priority: number;
  paused: boolean;
  expires_session: string | null;
  last_collected_at: Date | null;
  last_analyzed_at: Date | null;
  last_collection_id: string | null;
}
interface Collector {
  collect(input: z.infer<typeof collectEvidenceInput>): Promise<SourceResult[]>;
}

export class ResearchStore {
  constructor(
    readonly pool: Pool,
    readonly sources: Collector = new ResearchSources(),
    readonly quant = new QuantRunner(),
  ) {}
  async settings(owner: string, input?: unknown) {
    if (input !== undefined) {
      const patch = settingsInput.parse(input);
      await transaction(this.pool, async (c) => {
        await lockOwner(c, owner);
        const current = (
          await c.query(
            "SELECT payload FROM research_settings WHERE owner_id=$1",
            [owner],
          )
        ).rows[0]?.payload;
        const merged = { ...defaultResearchSettings, ...current, ...patch };
        if (merged.dailyCandidateLimit > merged.dailyResearchLimit)
          throw new DomainError("candidate_limit_exceeds_research_limit", 400);
        await c.query(
          `INSERT INTO research_settings(owner_id,payload) VALUES($1,$2)
          ON CONFLICT(owner_id) DO UPDATE SET payload=$2::jsonb,updated_at=now()`,
          [owner, JSON.stringify(merged)],
        );
      });
    }
    const row = (
      await this.pool.query(
        "SELECT payload FROM research_settings WHERE owner_id=$1",
        [owner],
      )
    ).rows[0];
    return { ...defaultResearchSettings, ...row?.payload };
  }
  async syncCoverage(owner: string) {
    return transaction(this.pool, async (c) => {
      await lockOwner(c, owner);
      // Only the latest version of each portfolio contributes live holdings.
      const holdings = (
        await c.query(
          `SELECT DISTINCT p->>'symbol' AS symbol FROM (
          SELECT DISTINCT ON (portfolio_id) positions FROM portfolio_snapshots WHERE owner_id=$1 ORDER BY portfolio_id,ordinal DESC
        ) latest CROSS JOIN LATERAL jsonb_array_elements(positions) p WHERE (p->>'quantity')::numeric>0`,
          [owner],
        )
      ).rows.map((row) => row.symbol as string);
      const watched = (
        await c.query("SELECT symbol FROM watchlist WHERE owner_id=$1", [owner])
      ).rows.map((row) => row.symbol as string);
      // Disappearing holdings remain a candidate dossier, not an eternal holding.
      await c.query(
        `UPDATE research_coverage SET tier='candidate',priority=0,paused=true,updated_at=now()
        WHERE owner_id=$1 AND ((tier='holding' AND NOT(symbol=ANY($2::text[]))) OR (tier='watchlist' AND NOT(symbol=ANY($3::text[]))))`,
        [owner, holdings, watched],
      );
      for (const ticker of [...new Set([...watched, ...holdings])]) {
        const tier = holdings.includes(ticker) ? "holding" : "watchlist";
        await c.query(
          `INSERT INTO research_coverage(owner_id,symbol,tier,priority) VALUES($1,$2,$3,$4)
          ON CONFLICT(owner_id,symbol) DO UPDATE SET tier=$3,priority=$4,expires_at=NULL,expires_session=NULL,updated_at=now()`,
          [owner, ticker, tier, tier === "holding" ? 100 : 80],
        );
      }
    });
  }
  async listCoverage(owner: string): Promise<ResearchCoverage[]> {
    await this.syncCoverage(owner);
    return (
      await this.pool.query<ResearchCoverage>(
        `SELECT *,expires_session::text FROM research_coverage WHERE owner_id=$1
      ORDER BY priority DESC,symbol`,
        [owner],
      )
    ).rows;
  }
  async updateCoverage(owner: string, input: unknown) {
    const request = coverageInput.parse(input);
    await this.syncCoverage(owner);
    const resumed =
      request.action === "resume"
        ? await this.quant.run({
            operation: "research_schedule",
            asOf: new Date().toISOString(),
          })
        : undefined;
    await transaction(this.pool, async (c) => {
      await lockOwner(c, owner);
      if (request.action === "watch") {
        await c.query(
          "INSERT INTO watchlist(owner_id,symbol) VALUES($1,$2) ON CONFLICT DO NOTHING",
          [owner, request.symbol],
        );
        await c.query(
          `INSERT INTO research_coverage(owner_id,symbol,tier,priority) VALUES($1,$2,'watchlist',80)
          ON CONFLICT(owner_id,symbol) DO UPDATE SET tier=CASE WHEN research_coverage.tier='holding' THEN 'holding' ELSE 'watchlist' END,
          priority=CASE WHEN research_coverage.tier='holding' THEN 100 ELSE 80 END,paused=false,expires_session=NULL,expires_at=NULL,updated_at=now()`,
          [owner, request.symbol],
        );
      } else if (request.action === "remove") {
        await c.query("DELETE FROM watchlist WHERE owner_id=$1 AND symbol=$2", [
          owner,
          request.symbol,
        ]);
        // A held asset can stop notifications, but remains visible as a holding.
        await c.query(
          "UPDATE research_coverage SET paused=true,updated_at=now() WHERE owner_id=$1 AND symbol=$2 AND tier='holding'",
          [owner, request.symbol],
        );
        await c.query(
          "DELETE FROM research_coverage WHERE owner_id=$1 AND symbol=$2 AND tier<>'holding'",
          [owner, request.symbol],
        );
      } else {
        const updated = await c.query(
          `UPDATE research_coverage SET paused=$3,updated_at=now(),
          expires_session=CASE WHEN NOT $3 AND tier IN ('temporary','candidate') THEN $4::date ELSE expires_session END
          WHERE owner_id=$1 AND symbol=$2 RETURNING symbol`,
          [
            owner,
            request.symbol,
            request.action === "pause",
            resumed?.temporaryExpirySession ?? null,
          ],
        );
        if (!updated.rowCount) throw new DomainError("not_found", 404);
      }
    });
    return this.listCoverage(owner);
  }
  async touchTemporary(owner: string, ticker: string) {
    symbol.parse(ticker);
    const schedule = await this.quant.run({
      operation: "research_schedule",
      asOf: new Date().toISOString(),
    });
    await this.pool.query(
      `INSERT INTO research_coverage(owner_id,symbol,tier,priority,expires_session) VALUES($1,$2,'temporary',60,$3)
      ON CONFLICT(owner_id,symbol) DO UPDATE SET tier=CASE WHEN research_coverage.tier IN ('holding','watchlist') THEN research_coverage.tier ELSE 'temporary' END,
      priority=GREATEST(research_coverage.priority,60),expires_session=CASE WHEN research_coverage.tier IN ('holding','watchlist') THEN NULL ELSE $3::date END,updated_at=now()`,
      [owner, ticker, schedule.temporaryExpirySession],
    );
  }
  async collect(
    ctx: { ownerId: string; taskId?: string; profileId?: string },
    input: unknown,
    key: string,
  ) {
    const request = collectEvidenceInput.parse(input);
    if (!key || key.length > 255)
      throw new DomainError("invalid_operation_key", 400);
    if (request.end && request.end > new Date().toISOString().slice(0, 10))
      throw new DomainError("future_evidence_window", 400);
    const scopedKey = ctx.taskId ? `${ctx.taskId}:${key}` : key;
    const requestHash = sha256(canonical(request));
    const id = randomUUID();
    const started = await transaction(this.pool, async (c) => {
      await lockOwner(c, ctx.ownerId);
      const old = (
        await c.query(
          "SELECT * FROM research_collection_runs WHERE owner_id=$1 AND operation_key=$2",
          [ctx.ownerId, scopedKey],
        )
      ).rows[0];
      if (old) {
        if (old.request_hash !== requestHash)
          throw new DomainError("operation_key_conflict", 409);
        if (
          old.status === "running" &&
          Date.now() - new Date(old.created_at).getTime() > 15 * 60_000
        ) {
          // Only read-only network collection is retried. No model call or published prediction is repeated.
          await c.query(
            "UPDATE research_collection_runs SET created_at=now() WHERE id=$1",
            [old.id],
          );
          return { id: old.id as string, run: true };
        }
        return {
          id: old.id as string,
          run: false,
          result: { collectionRef: old.id, status: old.status, ...old.payload },
        };
      }
      await c.query(
        "INSERT INTO research_collection_runs(id,owner_id,symbol,operation_key,request_hash,status) VALUES($1,$2,$3,$4,$5,'running')",
        [id, ctx.ownerId, request.symbol, scopedKey, requestHash],
      );
      return { id, run: true };
    });
    if (!started.run) return started.result!;
    let collected: SourceResult[];
    try {
      collected = await this.sources.collect(request);
    } catch {
      collected = [
        {
          source: request.sources?.[0] ?? "web",
          status: "unavailable",
          documents: [],
          failures: ["collector_failed"],
        },
      ];
    }
    return transaction(this.pool, async (c) => {
      const evidenceRefs: string[] = [];
      for (const source of collected) {
        let accepted = 0;
        for (const document of source.documents) {
          const observed = new Date(document.observedAt),
            published = document.publishedAt
              ? new Date(document.publishedAt)
              : null;
          if (
            !Number.isFinite(observed.getTime()) ||
            observed.getTime() > Date.now() + 1000 ||
            (published &&
              (!Number.isFinite(published.getTime()) || published > observed))
          ) {
            source.failures.push("invalid_evidence_time");
            continue;
          }
          const hash = sha256(
            canonical({
              publishedAt: document.publishedAt,
              payload: document.payload,
            }),
          );
          const saved = await c.query(
            `INSERT INTO research_evidence(id,owner_id,symbol,source,source_key,published_at,observed_at,content_hash,payload)
          VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT(owner_id,symbol,source,source_key,content_hash) DO NOTHING RETURNING id`,
            [
              randomUUID(),
              ctx.ownerId,
              request.symbol,
              source.source,
              document.sourceKey,
              published,
              observed,
              hash,
              JSON.stringify(document.payload),
            ],
          );
          const reference =
            saved.rows[0]?.id ??
            (
              await c.query(
                `SELECT id FROM research_evidence WHERE owner_id=$1 AND symbol=$2 AND source=$3 AND source_key=$4 AND content_hash=$5`,
                [
                  ctx.ownerId,
                  request.symbol,
                  source.source,
                  document.sourceKey,
                  hash,
                ],
              )
            ).rows[0]!.id;
          evidenceRefs.push(reference);
          accepted++;
        }
        if (source.failures.length)
          source.status = accepted ? "partial" : "unavailable";
      }
      const failures = collected.flatMap((source) =>
        source.failures.map((failure) => ({
          source: source.source,
          reason: failure,
        })),
      );
      const successfulSources = collected.filter(
        (source) => source.status === "empty",
      ).length;
      const status = failures.length
        ? evidenceRefs.length || successfulSources
          ? "partial"
          : "unavailable"
        : "completed";
      const payload = {
        symbol: request.symbol,
        evidenceRefs: [...new Set(evidenceRefs)],
        fingerprint: sha256([...new Set(evidenceRefs)].sort().join(",")),
        sources: collected.map(({ source, status, documents }) => ({
          source,
          status,
          count: documents.length,
        })),
        failures,
        completedAt: new Date().toISOString(),
      };
      await c.query(
        "UPDATE research_collection_runs SET status=$2,payload=$3 WHERE id=$1",
        [started.id, status, JSON.stringify(payload)],
      );
      await c.query(
        "UPDATE research_coverage SET last_collected_at=now(),last_collection_id=$3 WHERE owner_id=$1 AND symbol=$2",
        [ctx.ownerId, request.symbol, started.id],
      );
      return { collectionRef: started.id, status, ...payload };
    });
  }
  async context(owner: string, ticker: string, asOf?: string, query?: string) {
    symbol.parse(ticker);
    const at = asOf ? new Date(asOf) : new Date();
    if (!Number.isFinite(at.getTime()) || at.getTime() > Date.now())
      throw new DomainError("invalid_context_time", 400);
    return transaction(this.pool, async (c) => {
      await c.query("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ");
      const evidence = (
        await c.query(
          `SELECT * FROM (SELECT DISTINCT ON(source,source_key) id,source,source_key,published_at,observed_at,payload
        FROM research_evidence WHERE owner_id=$1 AND symbol=$2 AND observed_at<=$3 AND (published_at IS NULL OR published_at<=$3)
        ORDER BY source,source_key,observed_at DESC) versions ORDER BY observed_at DESC LIMIT 80`,
          [owner, ticker, at],
        )
      ).rows;
      const portfolios = (
        await c.query(
          `SELECT DISTINCT ON(portfolio_id) id,portfolio_id,as_of_session::text,positions,cash_usd::text
        FROM portfolio_snapshots WHERE owner_id=$1 AND created_at<=$2 ORDER BY portfolio_id,ordinal DESC`,
          [owner, at],
        )
      ).rows;
      const datasets = (
        await c.query(
          `SELECT DISTINCT ON(d.metadata->>'adjustment',d.metadata->>'feed') d.id,d.metadata,d.created_at
        FROM datasets d JOIN tool_operations op ON op.id=d.operation_id JOIN tasks t ON t.id=op.task_id AND t.owner_id=d.owner_id
        JOIN thread_contexts tc ON tc.thread_id=t.thread_id AND tc.owner_id=d.owner_id
        JOIN execution_contexts ec ON ec.id=tc.context_id AND ec.owner_id=d.owner_id AND ec.kind='personal'
        WHERE d.owner_id=$1 AND d.metadata->>'symbol'=$2 AND d.metadata->>'kind'='ohlcv'
          AND d.created_at<=$3 AND (d.metadata->>'asOf')::timestamptz<=$3
        ORDER BY d.metadata->>'adjustment',d.metadata->>'feed',d.created_at DESC LIMIT 8`,
          [owner, ticker, at],
        )
      ).rows;
      const company =
        evidence.find((item) => item.payload.kind === "company")?.payload ??
        null;
      const peers = company?.sic
        ? (
            await c.query(
              `SELECT DISTINCT symbol FROM research_evidence
        WHERE owner_id=$1 AND symbol<>$2 AND payload->>'kind'='company' AND payload->>'sic'=$3 AND observed_at<=$4
          AND (published_at IS NULL OR published_at<=$4) ORDER BY symbol LIMIT 30`,
              [owner, ticker, String(company.sic), at],
            )
          ).rows.map((row) => row.symbol as string)
        : [];
      const narrative = evidence
        .map(
          (item) =>
            `${item.payload.form ?? ""} ${item.payload.title ?? ""} ${item.payload.summary ?? ""} ${String(item.payload.text ?? "").slice(0, 1000)}`,
        )
        .join(" ");
      const tagPatterns: Record<string, RegExp> = {
        earnings: /10-[KQ]|earnings|revenue|guidance/i,
        management: /chief executive|management|shareholder meeting|DEF 14A/i,
        product: /product|launch|shipment/i,
        macro: /interest rate|inflation|employment|Federal Reserve/i,
        capital_allocation: /buyback|repurchase|dividend/i,
        merger: /merger|acquisition|takeover/i,
        regulatory: /regulat|export control|antitrust/i,
      };
      const eventTags = Object.entries(tagPatterns)
        .filter(([, pattern]) => pattern.test(narrative))
        .map(([tag]) => tag);
      const forecasts = (
        await c.query(
          "SELECT id,horizon,published_at,target_session,payload FROM forecasts WHERE owner_id=$1 AND symbol=$2 AND published_at<=$3 ORDER BY published_at DESC LIMIT 12",
          [owner, ticker, at],
        )
      ).rows;
      const lessons = (
        await c.query(
          `SELECT l.id,l.title,l.scope,COALESCE(b.resulting_status,'shadow') AS status,v.id AS version_id,v.horizon,v.symbol,v.event_type,v.statement,v.applicability,v.invalidation,v.created_at
        FROM experience_lessons l JOIN LATERAL (SELECT * FROM experience_versions WHERE lesson_id=l.id AND owner_id=l.owner_id
          AND created_at<=$3 ORDER BY created_at DESC,id DESC LIMIT 1) v ON true
        LEFT JOIN LATERAL (SELECT resulting_status FROM experience_batches WHERE lesson_version_id=v.id AND owner_id=l.owner_id
          AND created_at<=$3 ORDER BY created_at DESC,id DESC LIMIT 1) b ON true
        WHERE l.owner_id=$1 AND COALESCE(b.resulting_status,'shadow')<>'retired'
          AND (v.symbol=$2 OR v.symbol IS NULL OR (l.scope='industry' AND v.symbol=ANY($5::text[]))
            OR (l.scope IN ('industry','general') AND v.event_type=ANY($6::text[]))
            OR ($4::text IS NOT NULL AND (l.title ILIKE $4 OR v.statement ILIKE $4)))
        ORDER BY (v.symbol=$2) DESC NULLS LAST,(COALESCE(b.resulting_status,'shadow')='active') DESC,v.created_at DESC LIMIT 12`,
          [
            owner,
            ticker,
            at,
            query ? `%${query.slice(0, 100)}%` : null,
            peers,
            eventTags,
          ],
        )
      ).rows;
      const coverage =
        (
          await c.query(
            "SELECT tier,priority,paused,expires_session::text FROM research_coverage WHERE owner_id=$1 AND symbol=$2",
            [owner, ticker],
          )
        ).rows[0] ?? null;
      const capabilities = sourceCapabilities();
      const activeLessons = lessons.filter((item) => item.status === "active");
      const trialCandidates = lessons
        .filter(
          (item) => item.status === "shadow" || item.status === "candidate",
        )
        .map((item) => ({
          version_id: item.version_id,
          horizon: item.horizon,
          scope: item.scope,
          event_type: item.event_type,
          applicability: item.applicability,
          invalidation: item.invalidation,
          symbol: item.symbol,
          created_at: item.created_at,
        }));
      // The immutable source retains its full extracted text. The prompt context carries bounded excerpts and source ids.
      const excerpted = evidence.map((item) => ({
        ...item,
        eventKey: sha256(`${item.source}:${item.source_key}`),
        payload: {
          ...item.payload,
          ...(typeof item.payload.text === "string" &&
          item.payload.text.length > 5000
            ? { text: item.payload.text.slice(0, 5000), contextExcerpt: true }
            : {}),
        },
      }));
      const snapshot = {
        symbol: ticker,
        asOf: at.toISOString(),
        evidenceRefs: evidence.map((item) => item.id),
        evidence: excerpted,
        datasetRefs: datasets.map((item) => item.id),
        datasets,
        dossier: {
          company,
          peerSymbols: peers,
          eventTags,
          relationBasis: "SEC SIC and observed document keywords",
        },
        portfolios,
        forecasts,
        lessons: activeLessons,
        trialCandidates,
        coverage,
        capabilities,
        experienceLevel:
          forecasts.length >= 10
            ? "established"
            : forecasts.length
              ? "building"
              : "cold_start",
        caveats: [
          "published_at=null means source publication time was not verified",
          "Company and industry experience are separate from measured stock-specific accuracy",
        ],
      };
      const contextRef = randomUUID();
      await c.query(
        "INSERT INTO research_contexts(id,owner_id,symbol,as_of,payload) VALUES($1,$2,$3,$4,$5)",
        [contextRef, owner, ticker, at, JSON.stringify(snapshot)],
      );
      return { contextRef, ...snapshot };
    });
  }
  prepareContext(owner: string, ticker: string, asOf?: string, query?: string) {
    return this.context(owner, ticker, asOf, query);
  }
  async loadContext(owner: string, id: string) {
    const row = (
      await this.pool.query(
        "SELECT id,payload FROM research_contexts WHERE owner_id=$1 AND id=$2",
        [owner, id],
      )
    ).rows[0];
    if (!row) throw new DomainError("not_found", 404);
    return { contextRef: row.id, ...row.payload };
  }
  async stock(owner: string, ticker: string) {
    symbol.parse(ticker);
    const [coverage, evidence, forecasts, lessons, collections] =
      await Promise.all([
        this.pool.query(
          "SELECT *,expires_session::text FROM research_coverage WHERE owner_id=$1 AND symbol=$2",
          [owner, ticker],
        ),
        this.pool.query(
          "SELECT id,source,published_at,observed_at,payload FROM research_evidence WHERE owner_id=$1 AND symbol=$2 ORDER BY observed_at DESC LIMIT 100",
          [owner, ticker],
        ),
        this.pool.query(
          "SELECT * FROM forecasts WHERE owner_id=$1 AND symbol=$2 ORDER BY published_at DESC LIMIT 100",
          [owner, ticker],
        ),
        this.pool.query(
          `SELECT l.*,v.statement,v.applicability,v.invalidation,v.horizon FROM experience_lessons l
        JOIN experience_versions v ON v.id=l.current_version_id AND v.owner_id=l.owner_id WHERE l.owner_id=$1 AND (v.symbol=$2 OR v.symbol IS NULL) ORDER BY l.created_at DESC LIMIT 50`,
          [owner, ticker],
        ),
        this.pool.query(
          "SELECT id,status,payload,created_at FROM research_collection_runs WHERE owner_id=$1 AND symbol=$2 ORDER BY created_at DESC LIMIT 5",
          [owner, ticker],
        ),
      ]);
    const counts = (
      await this.pool.query(
        `SELECT
      (SELECT count(*)::int FROM research_evidence WHERE owner_id=$1 AND symbol=$2) AS evidence,
      (SELECT count(*)::int FROM forecasts WHERE owner_id=$1 AND symbol=$2) AS forecasts,
      (SELECT count(*)::int FROM experience_lessons l JOIN experience_versions v ON v.id=l.current_version_id WHERE l.owner_id=$1 AND (v.symbol=$2 OR v.symbol IS NULL)) AS lessons`,
        [owner, ticker],
      )
    ).rows[0];
    return {
      symbol: ticker,
      coverage: coverage.rows[0] ?? null,
      evidence: evidence.rows,
      forecasts: forecasts.rows,
      lessons: lessons.rows,
      collections: collections.rows,
      capabilities: sourceCapabilities(),
      counts,
    };
  }
}
