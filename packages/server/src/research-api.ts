import type { Pool } from "pg";
import { z } from "zod";
import { symbol, uuid } from "./business.js";
import { ForecastStore } from "./forecast-store.js";
import { ExperienceStore } from "./experience-store.js";
import { NotificationStore } from "./notifications.js";
import { ResearchStore, sourceCapabilities } from "./research-store.js";

/** Authenticated personal research views. Public callers never supply an owner. */
export class ResearchAPI {
  readonly research: ResearchStore;
  readonly forecasts: ForecastStore;
  readonly experiences: ExperienceStore;
  readonly notifications: NotificationStore;
  constructor(readonly pool: Pool) {
    this.research = new ResearchStore(pool);
    this.forecasts = new ForecastStore(pool);
    this.experiences = new ExperienceStore(pool);
    this.notifications = new NotificationStore(pool);
  }
  private coverageView(rows: any[]) {
    return rows.map((row) => ({
      ...row,
      kind: row.tier,
      status: row.paused ? "paused" : "active",
      last_researched_at: row.last_analyzed_at,
    }));
  }
  private forecastView(rows: any[]) {
    return rows.map((row) => ({
      ...row,
      status: row.evaluation
        ? "evaluated"
        : row.pending_reason
          ? "pending_data"
          : "open",
    }));
  }
  async hub(owner: string) {
    const [
      coverage,
      forecasts,
      lessons,
      notifications,
      settings,
      jobs,
      counts,
      scores,
      scan,
      queue,
    ] = await Promise.all([
      this.research.listCoverage(owner),
      this.forecasts.list(owner, { limit: 200 }),
      this.experiences.list(owner),
      this.notifications.list(owner),
      this.research.settings(owner),
      this.pool.query(
        "SELECT id,symbol,tier,stage,status,error_code,created_at,updated_at FROM research_jobs WHERE owner_id=$1 ORDER BY created_at DESC LIMIT 100",
        [owner],
      ),
      this.pool.query(
        `SELECT count(*) FILTER(WHERE e.id IS NULL)::int AS open,
        count(e.id)::int AS evaluated,count(*) FILTER(WHERE e.id IS NULL AND c.forecast_id IS NOT NULL)::int AS pending_data
        FROM forecasts f LEFT JOIN forecast_evaluations e ON e.forecast_id=f.id
        LEFT JOIN forecast_checks c ON c.forecast_id=f.id WHERE f.owner_id=$1`,
        [owner],
      ),
      this.pool.query(
        `SELECT f.horizon,count(*)::int AS samples,
        avg((e.payload->'metrics'->>'brier')::numeric)::text AS brier,
        avg((e.payload->'metrics'->>'absoluteError')::numeric)::text AS mae,
        avg((e.payload->'metrics'->>'directionCorrect')::boolean::int)::text AS direction_accuracy,
        avg((e.payload->'metrics'->>'intervalCovered')::boolean::int)::text AS interval_coverage,
        avg((e.payload->'metrics'->>'intervalScore')::numeric)::text AS interval_score,
        avg((e.payload->>'excessReturn')::numeric)::text AS excess_return
        FROM forecasts f JOIN forecast_evaluations e ON e.forecast_id=f.id
        WHERE f.owner_id=$1 AND f.supersedes_id IS NULL GROUP BY f.horizon`,
        [owner],
      ),
      this.pool.query(
        "SELECT * FROM market_scan_runs WHERE owner_id=$1 ORDER BY session DESC,created_at DESC LIMIT 1",
        [owner],
      ),
      this.pool.query(
        "SELECT count(*)::int AS queued, count(*) FILTER(WHERE status='deferred')::int AS deferred FROM research_jobs WHERE owner_id=$1 AND status IN ('pending','running','deferred')",
        [owner],
      ),
    ]);
    const metrics = Object.fromEntries(
      scores.rows.map((r) => [
        String(r.horizon),
        {
          samples: r.samples,
          brier: r.brier,
          mae: r.mae,
          directionAccuracy: r.direction_accuracy,
          intervalCoverage: r.interval_coverage,
          intervalScore: r.interval_score,
          excessReturn: r.excess_return,
        },
      ]),
    );
    const latest = scan.rows[0];
    return {
      coverage: this.coverageView(coverage),
      forecasts: this.forecastView(forecasts),
      lessons,
      notifications,
      settings,
      capabilities: sourceCapabilities(),
      jobs: jobs.rows,
      summary: {
        covered: coverage.filter(
          (r) =>
            !r.paused &&
            (!r.expires_session ||
              r.expires_session >= new Date().toISOString().slice(0, 10)),
        ).length,
        open: counts.rows[0].open,
        evaluated: counts.rows[0].evaluated,
        pendingData: counts.rows[0].pending_data,
        queued: queue.rows[0].queued,
        deferred: queue.rows[0].deferred,
        byHorizon: metrics,
      },
      scan: latest ? { ...latest, ...latest.payload } : null,
    };
  }
  async stock(owner: string, ticker: string) {
    const stock = await this.research.stock(owner, symbol.parse(ticker));
    const forecasts = await this.forecasts.list(owner, {
      symbol: ticker,
      limit: 100,
    });
    const dossier = (
      await this.pool.query(
        `SELECT r.payload FROM results r JOIN research_contexts rc ON rc.id=(r.lineage->>'researchContextRef')::uuid AND rc.owner_id=r.owner_id
      WHERE r.owner_id=$1 AND rc.symbol=$2 AND r.kind='research_summary' ORDER BY r.created_at DESC LIMIT 1`,
        [owner, ticker],
      )
    ).rows[0];
    return {
      ...stock,
      forecastCount: stock.counts.forecasts,
      forecasts: this.forecastView(forecasts),
      dossier: dossier ? { summary: dossier.payload.text } : null,
    };
  }
  async forecast(owner: string, id: string) {
    const forecast = await this.forecasts.detail(owner, uuid.parse(id));
    const evidence = (
      await this.pool.query(
        "SELECT id,source,published_at,observed_at,payload FROM research_evidence WHERE owner_id=$1 AND id=ANY($2::uuid[]) ORDER BY observed_at",
        [owner, forecast.payload.evidenceRefs ?? []],
      )
    ).rows;
    return {
      ...forecast,
      evidence,
      status: forecast.evaluation
        ? "evaluated"
        : forecast.pending_reason
          ? "pending_data"
          : "open",
    };
  }
  async handle(
    owner: string,
    path: string[],
    method: string,
    readBody: () => Promise<unknown>,
  ): Promise<unknown | undefined> {
    if (path.length === 1 && path[0] === "research-hub" && method === "GET")
      return this.hub(owner);
    if (path.length === 1 && path[0] === "coverage") {
      if (method === "GET")
        return this.coverageView(await this.research.listCoverage(owner));
      if (method === "PUT")
        return this.coverageView(
          await this.research.updateCoverage(owner, await readBody()),
        );
    }
    if (path.length === 1 && path[0] === "agent-settings") {
      if (method === "GET") return this.research.settings(owner);
      if (method === "PUT")
        return this.research.settings(owner, await readBody());
    }
    if (path.length === 2 && path[0] === "stocks" && method === "GET")
      return this.stock(owner, path[1]!);
    if (path.length === 2 && path[0] === "forecasts" && method === "GET")
      return this.forecast(owner, path[1]!);
    if (path.length === 1 && path[0] === "experiences" && method === "GET")
      return this.experiences.list(owner);
    if (path.length === 1 && path[0] === "notifications" && method === "GET")
      return this.notifications.list(owner);
    if (
      path.length === 3 &&
      path[0] === "notifications" &&
      path[2] === "read" &&
      method === "POST"
    ) {
      z.object({})
        .strict()
        .parse(await readBody());
      await this.notifications.markRead(owner, uuid.parse(path[1]));
      return { ok: true };
    }
    return undefined;
  }
}
