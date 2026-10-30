import { randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import { z } from "zod";
import { instantSchema, sha256Schema, type ResearchEvidence } from "@yta/contracts";
import { evidenceAvailableBy, validateResearchReport } from "./research-report.js";
import type { AlphaClient } from "./alpha.js";
import type { Config } from "./config.js";
import { ApiError } from "./errors.js";
import { ResearchRepository, terminalTypes, type EventBody, type Run } from "./research-store.js";

export class ConfirmedRunCancellation extends ApiError {
  constructor(){super("RUN_CANCELED","Upstream cancellation confirmed",409);}
}

export interface ResearchDriver {
  readonly runtime?: "hermes";
  readonly cancellationPolicy?: "confirmed-upstream";
  resume?(run:Run,emit:(body:EventBody)=>Promise<unknown>,signal:AbortSignal):Promise<void>;
  execute(run: Run, emit: (body: EventBody) => Promise<unknown>, signal: AbortSignal): Promise<void>;
}

/** A deterministic integration fixture. This is not a Hermes or LLM implementation. */
export class ReplayResearchDriver implements ResearchDriver {
  constructor(private alpha: AlphaClient) {}
  async execute(run: Run, emit: (body: EventBody) => Promise<unknown>, signal: AbortSignal) {
    if (run.data_mode !== "REPLAY") throw new ApiError("REPLAY_DISABLED", "Replay driver cannot execute real research", 503);
    const tool = async <T>(name: string, work: () => Promise<T>): Promise<{ id: string; value: T }> => {
      signal.throwIfAborted();
      const id = randomUUID();
      await emit({ type: "tool.started", payload: { tool: name, tool_call_id: id } });
      try {
        const result = await work();
        signal.throwIfAborted();
        await emit({ type: "tool.completed", payload: { tool: name, tool_call_id: id, success: true } });
        return { id, value: result };
      } catch (error) {
        await emit({ type: "tool.completed", payload: { tool: name, tool_call_id: id, success: false } });
        throw error;
      }
    };
    const snapshotCall = await tool("market_snapshot", () => this.alpha.request("/internal/v1/market/evidence", run.owner_id, undefined, undefined, signal));
    const evidenceEnvelope = z.object({ data_snapshot_hash: sha256Schema, snapshot: z.object({
      symbol: z.literal("NVDA"), as_of: instantSchema, data_mode: z.literal("REPLAY"),
      provider: z.literal("synthetic-fixture"), source_id: z.string().min(1).max(1000),
      bars: z.array(z.object({ available_at: instantSchema })).min(1).max(10000),
      splits: z.array(z.object({ available_at: instantSchema })).max(10000),
    }) }).parse(snapshotCall.value);
    const market = evidenceEnvelope.snapshot;
    const indicatorCall = await tool("indicators", () => this.alpha.request("/internal/v1/market/indicators", run.owner_id, undefined, undefined, signal));
    const indicator = z.object({ data_snapshot_hash: sha256Schema, as_of: instantSchema, data_mode: z.literal("REPLAY") }).parse(indicatorCall.value);
    if (!evidenceAvailableBy(indicator.as_of, market.as_of) || !evidenceAvailableBy(market.as_of, indicator.as_of) || indicator.data_mode !== market.data_mode) throw new ApiError("RESEARCH_REPORT_INVALID", "Tool research scopes differ", 502);
    const availableAt = [...market.bars, ...market.splits].map(item => item.available_at)
      .reduce((latest, value) => evidenceAvailableBy(latest, value) ? value : latest);
    const evidence: ResearchEvidence[] = [
      { tool_call_id: snapshotCall.id, tool: "market_snapshot", source_id: market.source_id, data_snapshot_hash: evidenceEnvelope.data_snapshot_hash, available_at: availableAt },
      { tool_call_id: indicatorCall.id, tool: "indicators", source_id: market.source_id, data_snapshot_hash: indicator.data_snapshot_hash, available_at: availableAt },
    ];
    await tool("canvas_command", async () => emit({ type: "canvas.command", payload: { command_id: randomUUID(), session_id: run.session_id, run_id: run.id, version: 1, type: "chart.show", payload: { symbol: "NVDA", timeframe: "1D", indicators: ["RSI14", "MACD"], as_of: market.as_of, data_mode: "REPLAY" } } }));
    const { value: draft } = await tool("baseline_predict", () => this.alpha.draft(run.owner_id, run.id, signal));
    const paragraphs = [
      "这是合成数据的固定回放流程，不是真实行情研究或 Hermes 回答。\n\n",
      "已调用行情、指标和 baseline 工具，并打开 NVDA 日线。",
      `5 个交易日的规则基线收益为 ${draft.forecast.expected_return}（简单收益）。`,
      "该值没有概率或准确率承诺。你可以检查数据来源与预测截点，再决定是否冻结这条回放预测。",
    ];
    const report = validateResearchReport({
      schema_version: "research-report-v1", profile_id: "alpha-orchestrator", symbol: "NVDA",
      status: "DRAFT_READY", data_mode: "REPLAY", as_of: market.as_of,
      summary: paragraphs.join(""), risks: ["合成回放数据不能证明真实行情中的预测表现。"],
      evidence, draft_id: draft.draft_id,
    }, {
      data_mode: run.data_mode, as_of: market.as_of, evidence, failure_code: null,
      draft: { draft_id: draft.draft_id, data_snapshot_hash: draft.forecast.data_snapshot_hash,
        data_mode: draft.forecast.data_mode, as_of: draft.forecast.as_of },
    });
    await emit({ type: "prediction.draft", payload: draft });
    for (const text of paragraphs) {
      signal.throwIfAborted();
      await emit({ type: "message.delta", payload: { text } });
      await delay(40, undefined, { signal });
    }
    await emit({ type: "message.completed", payload: { text: paragraphs.join(""), report } });
  }
}

export class UnavailableResearchDriver implements ResearchDriver {
  async execute() { throw new ApiError("HERMES_UNVERIFIED", "Real Hermes runtime is not configured and verified", 503); }
}

export class ResearchRunner {
  private active = new Map<string, { controller: AbortController; task: Promise<void> }>();
  private stopping = false;
  private sweep?: NodeJS.Timeout;
  constructor(private repository: ResearchRepository, private driver: ResearchDriver, private config: Config) {}

  async start() {
    await this.resume();
    this.sweep = setInterval(() => { void this.resume().catch(() => console.error("research queue poll failed")); }, 2000);
    this.sweep.unref();
  }

  private async resume() {
    if (this.stopping) return;
    for (const run of await this.repository.pending()) this.launch(run);
  }

  launch(run: Run) {
    if (run.data_mode !== this.config.YTA_DATA_MODE || this.stopping || this.active.has(run.id) || this.active.size >= 2) return;
    const controller = new AbortController();
    const task = this.execute(run, controller).catch(() => {
      // Persisted admission remains discoverable by the next sweep. Never resend an admitted turn.
      console.error(JSON.stringify({ event: "research.worker_failure", run_id: run.id }));
    }).finally(() => this.active.delete(run.id));
    this.active.set(run.id, { controller, task });
  }

  private async execute(run: Run, controller: AbortController) {
    const lease = await this.repository.pool.connect();
    const key = `run-owner:${run.id}`;
    let locked = false;
    let timer: NodeJS.Timeout | undefined;
    let poll: NodeJS.Timeout | undefined;
    const onLeaseError = () => controller.abort(new ApiError("EXECUTION_UNKNOWN", "Worker database lease was lost", 503));
    lease.on("error", onLeaseError);
    try {
      locked = Boolean((await lease.query("SELECT pg_try_advisory_lock(hashtextextended($1,0)) AS acquired", [key])).rows[0]?.acquired);
      if (!locked) return;
      const past = await this.repository.events(run.owner_id, run.id, 0);
      const summary = await this.repository.runSummary(run.owner_id, run.id);
      if (["COMPLETED", "FAILED", "CANCELED", "EXECUTION_UNKNOWN"].includes(summary.status)) return;
      const recovering=past.some(e => e.type === "run.started");
      if (recovering && !this.driver.resume) {
        await this.repository.append(run, { type: "run.execution_unknown", payload: { code: "EXECUTION_UNKNOWN", message: "Previous worker stopped after admission; no automatic resubmission", request_id: run.id, retryable: false } });
        return;
      }
      if (!recovering && await this.repository.isCanceled(run.id)) {
        await this.repository.append(run, { type: "run.canceled", payload: {} }); return;
      }
      if(!recovering)await this.repository.append(run, { type: "run.started", payload: {} });
      timer = setTimeout(() => controller.abort(new ApiError("RUN_TIMED_OUT", "Research deadline exceeded", 408)), this.config.YTA_RUN_TIMEOUT_MS);
      poll = setInterval(() => {
        void this.repository.isCanceled(run.id).then(canceled => {
          if (canceled) controller.abort(new ApiError("RUN_CANCELED", "Research canceled", 409));
        }).catch(() => controller.abort(new ApiError("EXECUTION_UNKNOWN", "Cannot verify cancellation state", 503)));
      }, 100);
      if(recovering && await this.repository.isCanceled(run.id))controller.abort(new ApiError("RUN_CANCELED","Research canceled",409));
      await (recovering?this.driver.resume!.bind(this.driver):this.driver.execute.bind(this.driver))(run, body => this.repository.append(run, body), controller.signal);
      controller.signal.throwIfAborted();
      await this.repository.append(run, { type: "run.completed", payload: {} }, this.driver.cancellationPolicy === "confirmed-upstream");
    } catch (error) {
      const cause = this.driver.cancellationPolicy === "confirmed-upstream"
        ? error instanceof ConfirmedRunCancellation ? error
          : controller.signal.aborted ? new ApiError("EXECUTION_UNKNOWN", "Remote cancellation outcome is unconfirmed", 503) : error
        : controller.signal.aborted ? controller.signal.reason : error;
      if (cause instanceof ApiError && cause.code === "RUN_CANCELED") {
        await this.repository.append(run, { type: "run.canceled", payload: {} });
      } else {
        const known = cause instanceof ApiError ? cause : new ApiError("RESEARCH_FAILED", "Research failed", 500);
        await this.repository.append(run, { type: known.code === "EXECUTION_UNKNOWN" ? "run.execution_unknown" : "run.failed", payload: { code: known.code, message: known.message, request_id: run.id, retryable: false } });
      }
    } finally {
      if (timer) clearTimeout(timer);
      if (poll) clearInterval(poll);
      lease.removeListener("error", onLeaseError);
      if (locked) {
        try { await lease.query("SELECT pg_advisory_unlock(hashtextextended($1,0))", [key]); }
        catch { lease.release(true); return; }
      }
      lease.release();
    }
  }

  async close() {
    this.stopping = true;
    if (this.sweep) clearInterval(this.sweep);
    for (const { controller } of this.active.values()) controller.abort(new ApiError("EXECUTION_UNKNOWN", "Worker shutdown interrupted execution", 503));
    await Promise.allSettled([...this.active.values()].map(item => item.task));
  }
}
