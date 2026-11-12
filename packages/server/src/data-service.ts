import {
  awaitTaskVerification,
  requireVerifiedTask,
} from "./task-verification.js";
import { createHash, randomUUID } from "node:crypto";
import type { Pool, PoolClient } from "pg";
import { z } from "zod";
import { transaction, lockOwner } from "./db.js";
import { DomainError, type Task } from "./tasks.js";
import { symbol, uuid } from "./business.js";
import { QuantRunner } from "./quant-runner.js";
import type { MarketProvider } from "../../market/src/alpaca.js";

export const marketInput = z
  .object({
    symbol,
    start: z.iso.date(),
    end: z.iso.date(),
    adjustment: z.enum(["raw", "split"]),
  })
  .strict();
export const quantInput = z.discriminatedUnion("kind", [
  z
    .object({
      kind: z.literal("indicators"),
      datasetRef: uuid,
      displayStart: z.iso.date().optional(),
    })
    .strict(),
  z.object({ kind: z.literal("prediction"), datasetRef: uuid }).strict(),
  z
    .object({
      kind: z.literal("portfolio"),
      portfolioSnapshotRef: uuid,
      datasetRefs: z
        .array(uuid)
        .max(100)
        .refine((ids) => new Set(ids).size === ids.length),
    })
    .strict(),
]);
// Constructed by the authenticated tool gateway, never parsed from model arguments.
export interface ToolContext {
  ownerId: string;
  taskId: string;
  profileId: string;
}
interface Operation {
  id: string;
  status: string;
  request_hash: string;
  result: Record<string, unknown> | null;
  error_code: string | null;
}
interface Fact {
  kind: "dataset" | "result";
  type: string;
  payload: unknown;
  metadata?: Record<string, unknown>;
  lineage?: Record<string, unknown>;
}
function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.keys(value)
    .sort()
    .map(
      (key) =>
        `${JSON.stringify(key)}:${canonical((value as Record<string, unknown>)[key])}`,
    )
    .join(",")}}`;
}
const hash = (value: unknown) =>
  createHash("sha256").update(canonical(value)).digest("hex");
const roleFor = {
  indicators: "research",
  prediction: "prediction",
  portfolio: "portfolio",
};

export class DataService {
  constructor(
    readonly pool: Pool,
    private provider?: MarketProvider,
    private quant = new QuantRunner(),
  ) {}
  private async owned<T>(
    context: ToolContext,
    fn: (client: PoolClient, task: Task) => Promise<T>,
  ) {
    return transaction(this.pool, async (c) => {
      await lockOwner(c, context.ownerId);
      const task = (
        await c.query<Task>(
          `SELECT t.* FROM tasks t JOIN executions e ON e.task_id=t.id
        JOIN profile_instances p ON p.id::text=e.profile_id AND p.owner_id=t.owner_id AND p.role=t.role
        WHERE t.id=$1 AND t.owner_id=$2 AND e.profile_id=$3 AND e.submission_started_at IS NOT NULL`,
          [context.taskId, context.ownerId, context.profileId],
        )
      ).rows[0];
      if (!task) throw new DomainError("not_found", 404);
      return fn(c, task);
    });
  }
  private async begin(
    context: ToolContext,
    key: string,
    kind: "market" | "quant",
    request: any,
  ) {
    if (!/^[\x21-\x7e]{1,255}$/.test(key))
      throw new DomainError("invalid_operation_key", 400);
    return awaitTaskVerification(() =>
      this.owned(context, async (c, t) => {
        if (
          kind === "quant" &&
          roleFor[request.kind as keyof typeof roleFor] !== t.role
        )
          throw new DomainError("tool_not_allowed", 403);
        const requestHash = hash({ kind, request });
        const old = (
          await c.query<Operation>(
            "SELECT * FROM tool_operations WHERE task_id=$1 AND operation_key=$2",
            [t.id, key],
          )
        ).rows[0];
        if (old) {
          if (old.request_hash !== requestHash)
            throw new DomainError("operation_key_conflict");
          return { operation: old, execute: false };
        }
        requireVerifiedTask(t);
        if (
          t.status !== "running" ||
          t.cancel_requested_at ||
          t.failure_code ||
          t.deadline.getTime() <= Date.now()
        )
          throw new DomainError("tool_not_admissible");
        if (
          (
            await c.query(
              "SELECT 1 FROM tool_operations WHERE task_id=$1 AND status='unknown'",
              [t.id],
            )
          ).rowCount
        )
          throw new DomainError("tool_execution_unknown");
        const count = (
          await c.query(
            `SELECT count(*) FROM tool_operations o JOIN tasks child ON child.id=o.task_id WHERE child.root_id=$1`,
            [t.root_id],
          )
        ).rows[0];
        if (Number(count.count) >= 100)
          throw new DomainError("tool_budget_exceeded", 429);
        const op = (
          await c.query<Operation>(
            `INSERT INTO tool_operations(id,owner_id,task_id,operation_key,kind,request_hash,request,status)
        VALUES($1,$2,$3,$4,$5,$6,$7,'running') RETURNING *`,
            [
              randomUUID(),
              context.ownerId,
              t.id,
              key,
              kind,
              requestHash,
              JSON.stringify(request),
            ],
          )
        ).rows[0]!;
        return { operation: op, execute: true };
      }),
    );
  }
  async get(context: ToolContext, id: string) {
    return this.owned(context, async (c) => {
      const row = (
        await c.query<Operation>(
          "SELECT id,status,result,error_code FROM tool_operations WHERE id=$1 AND task_id=$2 AND owner_id=$3",
          [id, context.taskId, context.ownerId],
        )
      ).rows[0];
      if (!row) throw new DomainError("not_found", 404);
      return row;
    });
  }
  async market(context: ToolContext, operationKey: string, input: unknown) {
    const request = marketInput.parse(input);
    return this.execute(
      context,
      operationKey,
      "market",
      request,
      async (signal) => {
        if (!this.provider)
          throw new DomainError("market_provider_unavailable", 503);
        const fetchedAt = new Date().toISOString();
        const plan = await this.quant.run(
          {
            operation: "plan_market",
            start: request.start,
            end: request.end,
            asOf: fetchedAt,
          },
          signal,
        );
        const rows = await this.provider.bars(
          { ...request, start: plan.start, end: plan.end },
          signal,
        );
        const payload = await this.quant.run(
          { operation: "validate_market", plan, rows },
          signal,
        );
        return {
          kind: "dataset",
          type: "ohlcv",
          payload,
          metadata: {
            kind: "ohlcv",
            symbol: request.symbol,
            interval: "1d",
            exchangeTimezone: "America/New_York",
            calendar: "XNYS",
            currency: "USD",
            sessionRange: { start: plan.start, end: plan.end },
            displayRange: { start: plan.displayStart, end: plan.displayEnd },
            asOf: plan.dataAsOf,
            fetchedAt,
            source: this.provider.source,
            feed: this.provider.feed,
            providerVersion: this.provider.version,
            adjustment: request.adjustment,
            schemaVersion: 1,
            missingSessions: payload.missingSessions,
            finalizationDelaySeconds: 1200,
            pointInTimeVerified: false,
          },
        };
      },
    );
  }
  private async dataset(context: ToolContext, id: string) {
    const row = (
      await this.pool.query(
        `SELECT d.* FROM datasets d JOIN tool_operations op ON op.id=d.operation_id
         JOIN tasks source ON source.id=op.task_id JOIN thread_contexts sc ON sc.thread_id=source.thread_id
         JOIN tasks target ON target.id=$3 AND target.owner_id=d.owner_id
         JOIN thread_contexts tc ON tc.thread_id=target.thread_id AND tc.context_id=sc.context_id AND tc.owner_id=sc.owner_id
         WHERE d.id=$1 AND d.owner_id=$2`,
        [id, context.ownerId, context.taskId],
      )
    ).rows[0];
    if (!row) throw new DomainError("not_found", 404);
    const m = row.metadata;
    if (
      m.kind !== "ohlcv" ||
      m.currency !== "USD" ||
      m.exchangeTimezone !== "America/New_York" ||
      m.interval !== "1d" ||
      !row.operation_id
    )
      throw new DomainError("incompatible_dataset", 422);
    if (hash(row.payload) !== row.content_hash)
      throw new DomainError("dataset_hash_mismatch", 503);
    return row;
  }
  async calculate(context: ToolContext, operationKey: string, input: unknown) {
    const request = quantInput.parse(input);
    return this.execute(
      context,
      operationKey,
      "quant",
      request,
      async (signal) => {
        const datasets: any[] = [];
        let payload: any;
        if (request.kind === "portfolio") {
          const scope = (
            await this.pool.query(
              `SELECT c.kind FROM tasks t JOIN thread_contexts tc ON tc.thread_id=t.thread_id AND tc.owner_id=t.owner_id JOIN execution_contexts c ON c.id=tc.context_id WHERE t.id=$1 AND t.owner_id=$2`,
              [context.taskId, context.ownerId],
            )
          ).rows[0];
          if (scope?.kind !== "personal")
            throw new DomainError("private_portfolio_unavailable", 403);
          const snapshot = (
            await this.pool.query(
              `SELECT id,as_of_session::text,positions,cash_usd::text FROM portfolio_snapshots WHERE id=$1 AND owner_id=$2`,
              [request.portfolioSnapshotRef, context.ownerId],
            )
          ).rows[0];
          if (!snapshot) throw new DomainError("not_found", 404);
          const expectedSymbols = new Set(
            snapshot.positions.map((p: { symbol: string }) => p.symbol),
          );
          const used = new Set<string>(),
            quotes: Record<string, unknown> = {};
          for (const ref of request.datasetRefs) {
            const data = await this.dataset(context, ref),
              meta = data.metadata;
            if (!expectedSymbols.has(meta.symbol) || used.has(meta.symbol))
              throw new DomainError("portfolio_dataset_mismatch", 422);
            used.add(meta.symbol);
            datasets.push(data);
            // Incorrect date/adjustment is surfaced as an incomplete valuation by Quant.
            const row = data.payload.rows.find(
              (r: { session: string }) => r.session === snapshot.as_of_session,
            );
            if (row?.close !== null && row?.close !== undefined)
              quotes[meta.symbol] = {
                session: row.session,
                close: row.close,
                adjustment: meta.adjustment,
              };
          }
          payload = await this.quant.run(
            {
              operation: "portfolio",
              positions: snapshot.positions,
              cashUsd: snapshot.cash_usd,
              asOf: snapshot.as_of_session,
              quotes,
            },
            signal,
          );
          payload.status = payload.missing.length ? "partial" : "available";
        } else {
          const data = await this.dataset(context, request.datasetRef);
          datasets.push(data);
          const displayStart =
            request.kind === "indicators"
              ? (request.displayStart ?? data.metadata.displayRange.start)
              : undefined;
          if (
            displayStart &&
            (displayStart < data.metadata.displayRange.start ||
              displayStart > data.metadata.displayRange.end)
          )
            throw new DomainError("display_range_out_of_bounds", 422);
          payload = await this.quant.run(
            {
              operation: request.kind,
              rows: data.payload.rows,
              adjustment: data.metadata.adjustment,
              ...(displayStart ? { displayStart } : {}),
            },
            signal,
          );
        }
        return {
          kind: "result",
          type: request.kind,
          payload,
          lineage: {
            datasetRefs: datasets.map((d) => d.id),
            inputs: datasets.map((d) => ({
              datasetRef: d.id,
              contentHash: d.content_hash,
              metadata: d.metadata,
            })),
            ...(request.kind === "portfolio"
              ? { portfolioSnapshotRef: request.portfolioSnapshotRef }
              : {}),
            parameters: request,
            codeVersion: payload.codeVersion,
            computedAt: new Date().toISOString(),
            decimalPrecision: 34,
            status: payload.status,
            pointInTimeVerified: false,
          },
        };
      },
    );
  }
  private async execute(
    context: ToolContext,
    key: string,
    kind: "market" | "quant",
    request: unknown,
    work: (signal: AbortSignal) => Promise<Fact>,
  ) {
    const { operation, execute } = await this.begin(
      context,
      key,
      kind,
      request,
    );
    if (!execute) return operation;
    const abort = new AbortController();
    let polling = false;
    const poll = setInterval(async () => {
      if (polling) return;
      polling = true;
      try {
        const row = (
          await this.pool.query(
            `SELECT o.status,t.cancel_requested_at,t.failure_code,t.deadline FROM tool_operations o JOIN tasks t ON t.id=o.task_id WHERE o.id=$1`,
            [operation.id],
          )
        ).rows[0];
        if (
          !row ||
          row.status !== "running" ||
          row.cancel_requested_at ||
          row.failure_code ||
          row.deadline.getTime() <= Date.now()
        )
          abort.abort();
      } catch {
        abort.abort();
      } finally {
        polling = false;
      }
    }, 100);
    const timer = setTimeout(() => abort.abort(), 25_000);
    let fact: Fact | undefined, error: string | undefined;
    try {
      fact = await work(abort.signal);
    } catch (e) {
      error = e instanceof DomainError ? e.code : "data_dependency_unavailable";
    } finally {
      clearInterval(poll);
      clearTimeout(timer);
    }
    // Only this closure won begin(). Replays never enter work() or reach here.
    // A stale-age observation is not an exit receipt; the original awaited
    // executor returning is. In particular QuantRunner waits for child close.
    const exitedAt = new Date();
    const aborted = abort.signal.aborted;
    return this.owned(context, async (c, t) => {
      const current = (
        await c.query<Operation>("SELECT * FROM tool_operations WHERE id=$1", [
          operation.id,
        ])
      ).rows[0]!;
      if (!["running", "stopping", "unknown"].includes(current.status))
        return current;
      const cancelled =
        !!t.cancel_requested_at || current.status === "stopping";
      error ??=
        t.failure_code ||
        (t.deadline.getTime() <= exitedAt.getTime()
          ? "deadline_exceeded"
          : aborted
            ? "operation_interrupted"
            : undefined);
      let result: Record<string, unknown> | null = null;
      if (fact) {
        const id = randomUUID();
        if (fact.kind === "dataset") {
          const contentHash = hash(fact.payload);
          await c.query(
            "INSERT INTO datasets(id,owner_id,metadata,payload,content_hash,operation_id) VALUES($1,$2,$3,$4,$5,$6)",
            [
              id,
              context.ownerId,
              JSON.stringify({ ...fact.metadata, version: id, contentHash }),
              JSON.stringify(fact.payload),
              contentHash,
              operation.id,
            ],
          );
          result = { datasetRef: id };
        } else {
          await c.query(
            "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage,operation_id) VALUES($1,$2,$3,$4,$5,$6,$7)",
            [
              id,
              context.ownerId,
              t.id,
              fact.type,
              JSON.stringify(fact.payload),
              JSON.stringify(fact.lineage),
              operation.id,
            ],
          );
          result = { resultRef: id };
        }
        if (cancelled || error) result.partial = true;
      }
      const status = cancelled ? "cancelled" : error ? "failed" : "completed";
      const errorCode = cancelled ? "operation_cancelled" : (error ?? null);
      await c.query(
        `INSERT INTO tool_exit_receipts(operation_id,task_id,owner_id,status,result,error_code,exited_at)
         VALUES($1,$2,$3,$4,$5,$6,$7)`,
        [
          operation.id,
          t.id,
          context.ownerId,
          status,
          result ? JSON.stringify(result) : null,
          errorCode,
          exitedAt,
        ],
      );
      return (
        await c.query<Operation>(
          "UPDATE tool_operations SET status=$2,result=$3,error_code=$4,finished_at=now() WHERE id=$1 RETURNING id,status,result,error_code",
          [
            operation.id,
            status,
            result ? JSON.stringify(result) : null,
            errorCode,
          ],
        )
      ).rows[0]!;
    });
  }
}
