import {
  awaitTaskVerification,
  requireVerifiedTask,
} from "./task-verification.js";
import { randomUUID } from "node:crypto";
import type { Pool, PoolClient } from "pg";
import { z } from "zod";
import { symbol, uuid } from "./business.js";
import { DomainError } from "./tasks.js";
import type { ToolContext } from "./data-service.js";
import {
  assertToolWrite,
  canonical,
  sha256,
  toolTransaction,
} from "./tool-context.js";
import { transaction, lockOwner } from "./db.js";
import { assertContextResources } from "./context-resources.js";
import { QuantRunner } from "./quant-runner.js";
import type { MarketProvider } from "../../market/src/alpaca.js";

const decimal = z
  .string()
  .max(64)
  .regex(/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/)
  .refine((v) => Number.isFinite(Number(v)) && Math.abs(Number(v)) <= 1e9);
export const forecastDistribution = z
  .object({
    meanReturn: decimal,
    lowerReturn: decimal,
    upperReturn: decimal,
    upProbability: decimal.refine((v) => Number(v) >= 0 && Number(v) <= 1),
  })
  .strict()
  .refine(
    (v) =>
      Number(v.lowerReturn) >= -1 &&
      Number(v.lowerReturn) <= Number(v.meanReturn) &&
      Number(v.meanReturn) <= Number(v.upperReturn),
    "invalid_forecast_distribution",
  );
export const forecastInput = z
  .object({
    symbol,
    horizon: z.union([z.literal(5), z.literal(20)]),
    meanReturn: decimal,
    lowerReturn: decimal,
    upperReturn: decimal,
    upProbability: decimal,
    researchContextRef: uuid,
    evidenceRefs: z.array(uuid).max(100),
    datasetRefs: z.array(uuid).max(20),
    lessonVersionRefs: z.array(uuid).max(20).default([]),
    eventKey: z.string().trim().min(1).max(200),
    thesis: z.string().trim().min(1).max(12000),
    invalidators: z.array(z.string().max(2000)).max(20).default([]),
    supersedesForecastId: uuid.optional(),
  })
  .strict()
  .refine(
    (v) =>
      forecastDistribution.safeParse({
        meanReturn: v.meanReturn,
        lowerReturn: v.lowerReturn,
        upperReturn: v.upperReturn,
        upProbability: v.upProbability,
      }).success,
    "invalid_forecast_distribution",
  );

export async function frozenContext(
  c: PoolClient,
  owner: string,
  id: string,
  stock: string,
  at: Date,
) {
  const row = (
    await c.query(
      "SELECT * FROM research_contexts WHERE id=$1 AND owner_id=$2 AND symbol=$3",
      [id, owner, stock],
    )
  ).rows[0];
  if (!row) throw new DomainError("research_context_not_found", 404);
  if (new Date(row.as_of) > at || new Date(row.created_at) > at)
    throw new DomainError("future_research_context", 422);
  return row;
}
export async function executionFingerprint(c: PoolClient, task: string) {
  const row = (
    await c.query(
      "SELECT profile_id,request,runtime_identity FROM executions WHERE task_id=$1",
      [task],
    )
  ).rows[0];
  return {
    methodVersion: "forward-forecast-v1",
    executionRequestHash: sha256(canonical(row?.request ?? {})),
    profileId: row?.profile_id,
    runtimeIdentity: row?.runtime_identity,
    modelVersion:
      process.env.FORECAST_MODEL_VERSION ?? "pinned-profile-runtime",
  };
}
export async function settleSnapshot(
  provider: MarketProvider,
  quant: QuantRunner,
  record: any,
  forecast: Record<string, unknown>,
  now: Date,
) {
  const signal = AbortSignal.timeout(25000);
  const query = {
    start: record.start_session,
    end: record.target_session,
    adjustment: "split" as const,
  };
  const [rows, benchmarkRows] = await Promise.all([
    provider.bars({ ...query, symbol: record.symbol }, signal),
    provider.bars({ ...query, symbol: "SPY" }, signal),
  ]);
  const outcome = await quant.run(
    {
      operation: "evaluate_forecast",
      startSession: query.start,
      targetSession: query.end,
      rows,
      benchmarkRows,
      ...forecast,
    },
    signal,
  );
  return {
    ...outcome,
    marketSnapshot: {
      source: provider.source,
      feed: provider.feed,
      providerVersion: provider.version,
      fetchedAt: now.toISOString(),
      adjustment: "split",
      pointInTimeVerified: false,
      contentHash: sha256(canonical({ rows, benchmarkRows })),
      rows,
      benchmarkRows,
    },
  };
}

export class ForecastStore {
  constructor(
    readonly pool: Pool,
    private quant = new QuantRunner(),
    private clock = () => new Date(),
  ) {}
  async publish(ctx: ToolContext, input: unknown, key: string) {
    const request = forecastInput.parse(input),
      requestHash = sha256(canonical(request));
    if (!/^[\x21-\x7e]{1,255}$/.test(key))
      throw new DomainError("invalid_operation_key", 400);
    return awaitTaskVerification(() =>
      toolTransaction(this.pool, ctx, async (c, t) => {
        const old = (
          await c.query(
            "SELECT id,result_id,request_hash,supersedes_id FROM forecasts WHERE task_id=$1 AND operation_key=$2",
            [t.id, key],
          )
        ).rows[0];
        if (old) {
          if (old.request_hash !== requestHash)
            throw new DomainError("operation_key_conflict");
          return {
            forecastId: old.id,
            resultRef: old.result_id,
            supersedesForecastId: old.supersedes_id,
          };
        }
        requireVerifiedTask(t);
        assertToolWrite(t);
        if (t.role !== "prediction")
          throw new DomainError("tool_not_allowed", 403);
        const now = this.clock();
        if (!request.supersedesForecastId) {
          const same = (
            await c.query(
              `SELECT f.id,f.result_id,f.request_hash FROM forecasts f
                WHERE f.owner_id=$1 AND f.symbol=$2 AND f.horizon=$3 AND f.event_key=$4 AND f.target_close_at>$5
                  AND NOT EXISTS(SELECT 1 FROM forecasts newer WHERE newer.supersedes_id=f.id)
                ORDER BY f.published_at DESC LIMIT 1`,
              [
                ctx.ownerId,
                request.symbol,
                request.horizon,
                request.eventKey,
                now,
              ],
            )
          ).rows[0];
          if (same) {
            if (same.request_hash !== requestHash)
              throw new DomainError("forecast_revision_required", 422);
            return { forecastId: same.id, resultRef: same.result_id };
          }
        }
        const context = await frozenContext(
          c,
          ctx.ownerId,
          request.researchContextRef,
          request.symbol,
          now,
        );
        const cutoff = new Date(context.as_of);
        const contextRefs: string[] = context.payload.evidenceRefs ?? [];
        if (request.evidenceRefs.some((id) => !contextRefs.includes(id)))
          throw new DomainError("evidence_outside_frozen_context", 422);
        const evidence = (
          await c.query(
            "SELECT id,published_at,observed_at FROM research_evidence WHERE owner_id=$1 AND id=ANY($2::uuid[])",
            [ctx.ownerId, request.evidenceRefs],
          )
        ).rows;
        if (evidence.length !== new Set(request.evidenceRefs).size)
          throw new DomainError("evidence_not_found", 404);
        if (
          evidence.some(
            (e) =>
              new Date(e.observed_at) > cutoff ||
              (e.published_at && new Date(e.published_at) > cutoff),
          )
        )
          throw new DomainError("future_evidence", 422);
        if (
          request.datasetRefs.some(
            (id) => !(context.payload.datasetRefs ?? []).includes(id),
          )
        )
          throw new DomainError("dataset_outside_frozen_context", 422);
        await assertContextResources(
          c,
          ctx.ownerId,
          t.thread_id,
          "datasets",
          request.datasetRefs,
        );
        const datasets = (
          await c.query(
            "SELECT id,metadata,created_at FROM datasets WHERE owner_id=$1 AND id=ANY($2::uuid[])",
            [ctx.ownerId, request.datasetRefs],
          )
        ).rows;
        if (datasets.length !== new Set(request.datasetRefs).size)
          throw new DomainError("dataset_not_found", 404);
        if (
          datasets.some(
            (d) =>
              new Date(d.created_at) > cutoff ||
              !d.metadata.asOf ||
              new Date(d.metadata.asOf) > cutoff,
          )
        )
          throw new DomainError("future_dataset", 422);
        if (
          datasets.some(
            (d) =>
              d.metadata.symbol !== request.symbol ||
              d.metadata.adjustment !== "split",
          )
        )
          throw new DomainError("forecast_dataset_mismatch", 422);
        const contextLessons: string[] = (context.payload.lessons ?? [])
          .filter(
            (l: any) => l.status === "active" && l.horizon === request.horizon,
          )
          .map((l: any) => l.version_id);
        if (
          request.lessonVersionRefs.some((id) => !contextLessons.includes(id))
        )
          throw new DomainError("lesson_outside_frozen_context", 422);
        const lessons = (
          await c.query(
            "SELECT v.id,v.created_at FROM experience_versions v JOIN experience_lessons l ON l.current_version_id=v.id WHERE v.owner_id=$1 AND v.id=ANY($2::uuid[]) AND l.status='active' AND v.horizon=$3 AND (l.scope<>'company' OR v.symbol=$4)",
            [
              ctx.ownerId,
              request.lessonVersionRefs,
              request.horizon,
              request.symbol,
            ],
          )
        ).rows;
        if (
          lessons.length !== new Set(request.lessonVersionRefs).size ||
          lessons.some((v) => new Date(v.created_at) > cutoff)
        )
          throw new DomainError("lesson_version_unavailable", 422);
        if (request.supersedesForecastId) {
          const prior = (
            await c.query(
              "SELECT symbol,horizon FROM forecasts WHERE id=$1 AND owner_id=$2",
              [request.supersedesForecastId, ctx.ownerId],
            )
          ).rows[0];
          if (
            !prior ||
            prior.symbol !== request.symbol ||
            prior.horizon !== request.horizon
          )
            throw new DomainError("forecast_revision_mismatch", 422);
        }
        await this.quant.run({
          operation: "validate_forecast",
          forecast: request,
        });
        const plan = await this.quant.run({
          operation: "forecast_plan",
          publishedAt: now.toISOString(),
          horizon: request.horizon,
        });
        if (this.clock() >= new Date(plan.startOpenAt))
          throw new DomainError("forecast_start_elapsed", 422);
        const id = randomUUID(),
          resultId = randomUUID();
        const payload = {
          ...request,
          ...plan,
          publishedAt: now.toISOString(),
          intervalCoverage: "0.8",
          returnBasis: "split-adjusted-price-return-open-to-close",
          subjective: true,
          ...(await executionFingerprint(c, t.id)),
        };
        await c.query(
          "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage) VALUES($1,$2,$3,'forecast',$4,$5)",
          [
            resultId,
            ctx.ownerId,
            t.id,
            JSON.stringify(payload),
            JSON.stringify({
              researchContextRef: context.id,
              evidenceRefs: request.evidenceRefs,
              datasetRefs: request.datasetRefs,
              lessonVersionRefs: request.lessonVersionRefs,
            }),
          ],
        );
        await c.query(
          `INSERT INTO forecasts(id,owner_id,task_id,result_id,operation_key,request_hash,symbol,horizon,event_key,research_context_id,published_at,start_session,target_session,start_open_at,target_close_at,evaluate_after,supersedes_id,payload)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)`,
          [
            id,
            ctx.ownerId,
            t.id,
            resultId,
            key,
            requestHash,
            request.symbol,
            request.horizon,
            request.eventKey,
            context.id,
            now,
            plan.startSession,
            plan.targetSession,
            plan.startOpenAt,
            plan.targetCloseAt,
            plan.evaluateAfter,
            request.supersedesForecastId ?? null,
            JSON.stringify(payload),
          ],
        );
        return {
          forecastId: id,
          resultRef: resultId,
          supersedesForecastId: request.supersedesForecastId,
        };
      }),
    );
  }
  async list(owner: string, filter: { symbol?: string; limit?: number } = {}) {
    return (
      await this.pool.query(
        `SELECT f.id,f.symbol,f.horizon,f.published_at,f.start_session::text,f.target_session::text,f.result_id,f.supersedes_id,f.payload,e.result_id AS evaluation_ref,e.payload AS evaluation,c.reason AS pending_reason
      FROM forecasts f LEFT JOIN forecast_evaluations e ON e.forecast_id=f.id LEFT JOIN forecast_checks c ON c.forecast_id=f.id
      WHERE f.owner_id=$1 AND ($2::text IS NULL OR f.symbol=$2) ORDER BY f.published_at DESC LIMIT $3`,
        [
          owner,
          filter.symbol ?? null,
          Math.min(200, Math.max(1, filter.limit ?? 50)),
        ],
      )
    ).rows;
  }
  async detail(owner: string, id: string) {
    const f = (
      await this.pool.query(
        "SELECT *,start_session::text,target_session::text FROM forecasts WHERE id=$1 AND owner_id=$2",
        [id, owner],
      )
    ).rows[0];
    if (!f) throw new DomainError("not_found", 404);
    return {
      ...f,
      pending_reason:
        (
          await this.pool.query(
            "SELECT reason FROM forecast_checks WHERE forecast_id=$1",
            [id],
          )
        ).rows[0]?.reason ?? null,
      evaluation:
        (
          await this.pool.query(
            "SELECT * FROM forecast_evaluations WHERE forecast_id=$1 AND owner_id=$2",
            [id, owner],
          )
        ).rows[0] ?? null,
      reviews: (
        await this.pool.query(
          "SELECT * FROM forecast_reviews WHERE forecast_id=$1 AND owner_id=$2 ORDER BY created_at",
          [id, owner],
        )
      ).rows,
    };
  }
  async evaluateDue(
    provider: MarketProvider | undefined,
    now = new Date(),
    limit = 20,
  ) {
    const evaluated: Array<{
      forecastId: string;
      ownerId: string;
      symbol: string;
      evaluationRef: string;
    }> = [];
    let pending = 0,
      failed = 0;
    const due = (
      await this.pool.query(
        `SELECT f.*,f.start_session::text,f.target_session::text FROM forecasts f LEFT JOIN forecast_evaluations e ON e.forecast_id=f.id LEFT JOIN forecast_checks checks ON checks.forecast_id=f.id
      WHERE e.id IS NULL AND f.evaluate_after<=$1 ORDER BY checks.checked_at NULLS FIRST,f.evaluate_after LIMIT $2`,
        [now, limit],
      )
    ).rows;
    if (!provider) {
      for (const f of due)
        await this.check(f.id, now, "market_provider_unavailable");
      return { evaluated, pending: due.length, failed };
    }
    for (const f of due) {
      try {
        const outcome = await settleSnapshot(
          provider,
          this.quant,
          f,
          { forecast: f.payload },
          now,
        );
        if (outcome.status !== "resolved") {
          pending++;
          await this.check(f.id, now, outcome.reason);
          continue;
        }
        const item = await transaction(this.pool, async (c) => {
          await lockOwner(c, f.owner_id);
          if (
            (
              await c.query(
                "SELECT 1 FROM forecast_evaluations WHERE forecast_id=$1",
                [f.id],
              )
            ).rowCount
          )
            return null;
          const id = randomUUID(),
            resultId = randomUUID();
          await c.query(
            "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage) VALUES($1,$2,$3,'forecast_evaluation',$4,$5)",
            [
              resultId,
              f.owner_id,
              f.task_id,
              JSON.stringify(outcome),
              JSON.stringify({
                forecastId: f.id,
                forecastRef: f.result_id,
                codeVersion: outcome.codeVersion,
              }),
            ],
          );
          await c.query(
            "INSERT INTO forecast_evaluations(id,forecast_id,owner_id,result_id,payload) VALUES($1,$2,$3,$4,$5)",
            [id, f.id, f.owner_id, resultId, JSON.stringify(outcome)],
          );
          await c.query("DELETE FROM forecast_checks WHERE forecast_id=$1", [
            f.id,
          ]);
          return {
            forecastId: f.id,
            ownerId: f.owner_id,
            symbol: f.symbol,
            evaluationRef: resultId,
          };
        });
        if (item) evaluated.push(item);
      } catch (error) {
        failed++;
        await this.check(
          f.id,
          now,
          error instanceof DomainError ? error.code : "market_unavailable",
        );
      }
    }
    return { evaluated, pending, failed };
  }
  private async check(id: string, now: Date, reason: string) {
    await this.pool.query(
      "INSERT INTO forecast_checks(forecast_id,checked_at,reason) VALUES($1,$2,$3) ON CONFLICT(forecast_id) DO UPDATE SET checked_at=excluded.checked_at,reason=excluded.reason",
      [id, now, reason],
    );
  }
}
