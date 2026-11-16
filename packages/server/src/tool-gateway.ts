import { researchContextView } from "./research-context-view.js";
import { contextDeltaView } from "./research-context-delta.js";
import { assertContextResources } from "./context-resources.js";
import { ExecutionContextStore } from "./execution-context.js";
import { z } from "zod";
import type { Pool } from "pg";
import {
  DataService,
  marketInput,
  quantInput,
  type ToolContext,
} from "./data-service.js";
import {
  CanvasStore,
  canvasUpsert,
  canvasPatch,
  canvasRemove,
  summaryInput,
  canvasModelInput,
} from "./canvas-store.js";
import {
  ToolCapabilities,
  ToolCalls,
  toolTransaction,
} from "./tool-context.js";
import { TaskStore, DomainError } from "./tasks.js";
import { BusinessStore, uuid, symbol } from "./business.js";
import { ResearchStore, collectEvidenceInput } from "./research-store.js";
import { ForecastStore, forecastInput } from "./forecast-store.js";
import {
  ExperienceStore,
  reviewInput,
  forecastTrialArmInput,
} from "./experience-store.js";

const dispatch = z
  .object({
    role: z.enum(["prediction", "portfolio"]),
    requestKey: z.string().regex(/^[\x21-\x7e]{1,128}$/),
    question: z.string().trim().min(1).max(8000),
    datasetRefs: z
      .array(uuid)
      .max(100)
      .refine((ids) => ids.length === new Set(ids).size)
      .describe(
        "Prediction: split-adjusted history. Portfolio: raw prices covering the holdings date, or an empty array so the specialist fetches them. Never pass split-adjusted research history as portfolio quotes.",
      ),
    portfolioSnapshotRef: uuid.optional(),
    researchContextRef: uuid.optional(),
  })
  .strict()
  .refine((v) => v.role !== "portfolio" || !!v.portfolioSnapshotRef, {
    message: "portfolio_snapshot_required",
  });
const personalResearchTools = new Set([
  "read_research_context",
  "collect_evidence",
  "read_forecasts",
  "publish_forecast",
  "record_review",
  "get_trial_context",
  "publish_forecast_trial_arm",
]);
const tools = {
  read_research_context: {
    schema: z
      .object({
        symbol: symbol.optional(),
        contextRef: uuid.optional(),
        evidenceOffset: z.number().int().min(0).max(500).optional(),
        query: z.string().max(200).optional(),
      })
      .strict()
      .refine(
        (value) => !!value.symbol || !!value.contextRef,
        "symbol_or_context_required",
      ),
    roles: ["research", "prediction", "portfolio"],
    write: true,
    description:
      "Returns compact evidence pages; use the returned contextRef plus evidenceOffset=nextOffset to read more of the SAME frozen snapshot. Load a frozen personal stock research context by contextRef, or save a new snapshot with evidence, portfolio relationships, prior forecasts and relevant lessons. Pass the returned contextRef to the prediction specialist. New snapshots exclude subsequently observed evidence.",
  },
  collect_evidence: {
    schema: collectEvidenceInput,
    roles: ["research"],
    write: true,
    description:
      "Collect and persist company disclosures, news, web/IR pages and public comments, X discussion and macro vintages. Returns evidenceRefs, source status and explicit missing coverage. Reuse the same operation for retries, then freeze context with read_research_context.",
  },
  read_forecasts: {
    schema: z
      .object({ symbol: symbol.optional(), forecastId: uuid.optional() })
      .strict()
      .refine((v) => !!v.symbol || !!v.forecastId),
    roles: ["research", "prediction", "portfolio"],
    write: false,
    description:
      "Read saved forward forecasts and deterministic outcomes for a symbol or one forecastId. Revisions do not erase original predictions. Excludes shadow experiment arms.",
  },
  publish_forecast: {
    schema: forecastInput,
    roles: ["prediction"],
    write: true,
    description:
      "Publish one immutable 5- or 20-session model forecast from a frozen researchContextRef. All distribution values are decimal strings. The server fixes future first-open/target-close dates. Use supersedesForecastId for a revised judgment; old forecasts remain evaluable. Returns forecastId/resultRef for forecast_result Canvas.",
  },
  record_review: {
    schema: reviewInput,
    roles: ["research"],
    write: true,
    description:
      "Record a settled forecast review, distinguishing observed outcome from causal attribution. Optionally propose a scoped candidate lesson for subsequent isolated shadow trials. One review cannot automatically promote a lesson.",
  },
  get_trial_context: {
    schema: z.object({}).strict(),
    roles: ["prediction"],
    write: false,
    description:
      "Read only the frozen evidence and method assigned to this isolated shadow experiment arm. Available only for an assigned trial task; never returns the other arm's output.",
  },
  publish_forecast_trial_arm: {
    schema: forecastTrialArmInput,
    roles: ["prediction"],
    write: true,
    description:
      "Persist this task's assigned control or candidate distribution before the planned opening time. It does not create a primary user forecast. Use only get_trial_context evidence.",
  },
  market_history: {
    schema: marketInput,
    roles: ["research", "prediction", "portfolio"],
    write: true,
    description:
      "Fetch and persist verified daily OHLCV. Returns an operation with datasetRef, or explicit failure.",
  },
  quant_calculate: {
    schema: quantInput,
    roles: ["research", "prediction", "portfolio"],
    write: true,
    description:
      "Compute from authorized references only. Research: indicators; prediction: prediction; portfolio: portfolio.",
  },
  read_dataset: {
    schema: z.object({ datasetRef: uuid }).strict(),
    roles: ["research", "prediction", "portfolio"],
    write: false,
    description: "Read a saved dataset and provenance.",
  },
  read_result: {
    schema: z.object({ resultRef: uuid }).strict(),
    roles: ["research", "prediction", "portfolio"],
    write: false,
    description: "Read an immutable calculation or research result.",
  },
  read_holdings: {
    schema: z.object({ portfolioSnapshotRef: uuid }).strict(),
    roles: ["research", "portfolio"],
    write: false,
    description:
      "Read the exact manual holdings version selected by the user. No holdings writes.",
  },
  get_operation: {
    schema: z.object({ operationId: uuid }).strict(),
    roles: ["research", "prediction", "portfolio"],
    write: false,
    description:
      "Query a pending Market/Quant operation without resubmitting it.",
  },
  submit_specialist: {
    schema: dispatch,
    roles: ["research"],
    write: true,
    description:
      "Dispatch at most one prediction and one portfolio specialist. Pass minimal question and authorized references; poll the returned childTaskId.",
  },
  get_specialist_result: {
    schema: z.object({ childTaskId: uuid }).strict(),
    roles: ["research"],
    write: false,
    description:
      "Read a direct specialist task and its saved result references.",
  },
  get_workspace: {
    schema: z.object({}).strict(),
    roles: ["research", "prediction", "portfolio"],
    write: false,
    description: "Read this research revision's Canvas.",
  },
  save_research_summary: {
    schema: summaryInput,
    roles: ["research"],
    write: true,
    description:
      "Save plain-text research interpretation with its evidence references. Use the returned resultRef for a research_summary Canvas component; specialist calculation references are evidence inputs, not research_summary resultRefs.",
  },
  canvas_upsert: {
    schema: canvasUpsert,
    roles: ["research", "prediction", "portfolio"],
    write: true,
    description:
      "Create or replace a component using saved references. stock_chart requires datasetRef. Other components require matching resultRef: indicator_panel=indicators, prediction_result=legacy prediction, forecast_result=forecast, forecast_evaluation=forecast_evaluation, review=review, portfolio_summary=portfolio, research_summary=save_research_summary result. Never use a specialist resultRef for research_summary. Omit artifactId when creating; reuse it only to replace a component.",
  },
  canvas_patch: {
    schema: canvasPatch,
    roles: ["research", "prediction", "portfolio"],
    write: true,
    description:
      "Replace title or display properties; never change factual data using patch.",
  },
  canvas_remove: {
    schema: canvasRemove,
    roles: ["research", "prediction", "portfolio"],
    write: true,
    description: "Remove a component from this research revision.",
  },
};
// Build-time export for the pinned Hermes plugin. Authorization still happens
// on every gateway call; publishing a schema grants no capability.
export function toolDescriptors(
  role: "research" | "prediction" | "portfolio" | "consult",
) {
  // A consult answer may read saved facts; it must never write or publish.
  return Object.entries(tools)
    .filter(([, t]) =>
      role === "consult"
        ? !t.write && t.roles.includes("research")
        : t.roles.includes(role),
    )
    .map(([name, t]) => ({
      name,
      description: t.description,
      inputSchema: z.toJSONSchema(
        name === "quant_calculate"
          ? quantInput.options.find(
              (option) =>
                option.shape.kind.value ===
                (role === "research" ? "indicators" : role),
            )!
          : name === "canvas_upsert"
            ? canvasModelInput(role)
            : t.schema,
      ),
    }));
}
export class ToolGateway {
  private calls: ToolCalls;
  private canvas: CanvasStore;
  private tasks: TaskStore;
  private business: BusinessStore;
  private research: ResearchStore;
  private forecasts: ForecastStore;
  private experiences: ExperienceStore;
  constructor(
    readonly pool: Pool,
    readonly data: DataService,
  ) {
    this.calls = new ToolCalls(pool);
    this.canvas = new CanvasStore(pool);
    this.tasks = new TaskStore(pool);
    this.business = new BusinessStore(pool);
    this.research = new ResearchStore(pool);
    this.forecasts = new ForecastStore(pool);
    this.experiences = new ExperienceStore(pool);
  }
  /**
   * A re-freeze after collection normally creates a new contextRef whose
   * evidence is mostly what the agent already read. Return only the new items
   * instead of making it page through the snapshot again.
   */
  private async contextDelta(
    ctx: ToolContext,
    snapshot: any,
    view: any,
  ): Promise<any> {
    const previous = (
      await this.pool.query(
        `SELECT response->>'contextRef' AS ref
         FROM tool_calls
         WHERE task_id=$1 AND owner_id=$2 AND tool='read_research_context'
           AND response IS NOT NULL AND response ? 'contextRef'
         ORDER BY created_at DESC LIMIT 1`,
        [ctx.taskId, ctx.ownerId],
      )
    ).rows[0];
    if (!previous?.ref || previous.ref === snapshot.contextRef) return view;
    let previousSnapshot: any;
    try {
      previousSnapshot = await this.research.loadContext(ctx.ownerId, previous.ref);
    } catch {
      return view;
    }
    if (previousSnapshot?.symbol !== snapshot.symbol) return view;
    return contextDeltaView(snapshot, view, previousSnapshot);
  }
  async list(ctx: ToolContext) {
    const task = await this.tasks.get(ctx.ownerId, ctx.taskId);
    const descriptors = toolDescriptors(task.role);
    if (task.input.trialId)
      return descriptors.filter((d) =>
        (task.role === "research"
          ? ["submit_specialist", "get_specialist_result"]
          : ["get_trial_context", "publish_forecast_trial_arm"]
        ).includes(d.name),
      );
    const context = await new ExecutionContextStore(this.pool).thread(
      ctx.ownerId,
      task.thread_id,
    );
    return context.kind === "personal"
      ? descriptors
      : descriptors.filter((d) => !personalResearchTools.has(d.name));
  }
  async invoke(ctx: ToolContext, name: string, input: unknown, key: string) {
    const spec = tools[name as keyof typeof tools];
    if (!spec) throw new DomainError("unknown_tool", 400);
    const args: any = spec.schema.parse(input);
    const task = await this.tasks.get(ctx.ownerId, ctx.taskId);
    if (
      task.input.trialId &&
      !(
        task.role === "research"
          ? ["submit_specialist", "get_specialist_result"]
          : ["get_trial_context", "publish_forecast_trial_arm"]
      ).includes(name)
    )
      throw new DomainError("trial_tool_not_allowed", 403);
    if (
      personalResearchTools.has(name) &&
      (
        await new ExecutionContextStore(this.pool).thread(
          ctx.ownerId,
          task.thread_id,
        )
      ).kind !== "personal"
    )
      throw new DomainError("private_research_unavailable", 403);
    if (!spec.roles.includes(task.role))
      throw new DomainError("tool_not_allowed", 403);
    if (name === "read_dataset" || name === "read_result")
      await assertContextResources(
        this.pool,
        ctx.ownerId,
        task.thread_id,
        name === "read_dataset" ? "datasets" : "results",
        [name === "read_dataset" ? args.datasetRef : args.resultRef],
      );
    if (
      name === "read_holdings" &&
      (
        await new ExecutionContextStore(this.pool).thread(
          ctx.ownerId,
          task.thread_id,
        )
      ).kind !== "personal"
    )
      throw new DomainError("private_portfolio_unavailable", 403);
    if (!spec.write) {
      switch (name) {
        case "read_forecasts":
          return args.forecastId
            ? this.forecasts.detail(ctx.ownerId, args.forecastId)
            : this.forecasts.list(ctx.ownerId, { symbol: args.symbol });
        case "get_trial_context":
          return this.experiences.getTrialContext(ctx);
        case "read_dataset":
          return this.business.resource(
            ctx.ownerId,
            "datasets",
            args.datasetRef,
          );
        case "read_result":
          return this.business.resource(ctx.ownerId, "results", args.resultRef);
        case "get_operation":
          return this.data.get(ctx, args.operationId);
        case "get_workspace":
          return this.canvas.workspace(ctx);
        case "get_specialist_result": {
          const until = Date.now() + 25_000;
          for (;;) {
            const child = await this.tasks.get(ctx.ownerId, args.childTaskId);
            if (child.parent_id !== ctx.taskId)
              throw new DomainError("not_found", 404);
            const parent = await this.tasks.get(ctx.ownerId, ctx.taskId);
            if (
              [
                "completed",
                "failed",
                "cancelled",
                "interrupted",
                "unknown",
              ].includes(child.status) ||
              parent.cancel_requested_at ||
              parent.failure_code ||
              Date.now() >= until
            )
              return {
                childTaskId: child.id,
                status: child.status,
                output: child.output,
              };
            await new Promise((resolve) => setTimeout(resolve, 500));
          }
        }
        case "read_holdings":
          return toolTransaction(this.pool, ctx, async (c, t) => {
            if (t.input.portfolioSnapshotRef !== args.portfolioSnapshotRef)
              throw new DomainError("portfolio_not_selected", 403);
            const row = (
              await c.query(
                "SELECT id,as_of_session::text,positions,cash_usd::text FROM portfolio_snapshots WHERE id=$1 AND owner_id=$2",
                [args.portfolioSnapshotRef, ctx.ownerId],
              )
            ).rows[0];
            if (!row) throw new DomainError("not_found", 404);
            return row;
          });
      }
    }
    const call = await this.calls.reserve(ctx, key, name, args);
    if (call.response !== null) return call.response;
    try {
      let response: unknown;
      switch (name) {
        case "read_research_context": {
          if (args.evidenceOffset && !args.contextRef)
            throw new DomainError("context_ref_required_for_pagination", 422);
          let snapshot: any;
          if (args.contextRef) {
            snapshot = await this.research.loadContext(
              ctx.ownerId,
              args.contextRef,
            );
            if (args.symbol && snapshot.symbol !== args.symbol)
              throw new DomainError("research_context_symbol_mismatch", 422);
          } else {
            if (!task.parent_id && !task.input.researchJobId)
              await this.research.touchTemporary(ctx.ownerId, args.symbol);
            snapshot = await this.research.context(
              ctx.ownerId,
              args.symbol,
              undefined,
              args.query,
            );
          }
          response = researchContextView(snapshot, args.evidenceOffset ?? 0);
          if (task.role === "prediction") {
            const { trialCandidates: _trialCandidates, ...primaryContext } =
              response as Record<string, unknown>;
            response = primaryContext;
          }
          // A fresh read (not explicit pagination or search) of a newer frozen
          // snapshot only needs the evidence added since the previous read.
          if (!args.evidenceOffset && !args.query)
            response = await this.contextDelta(
              ctx,
              snapshot,
              response as Record<string, unknown>,
            );
          break;
        }
        case "collect_evidence":
          response = await this.research.collect(ctx, args, call.id);
          break;
        case "publish_forecast":
          response = await this.forecasts.publish(ctx, args, call.id);
          break;
        case "record_review":
          response = await this.experiences.recordReview(ctx, args, call.id);
          break;
        case "publish_forecast_trial_arm":
          response = await this.experiences.publishTrialArm(ctx, args, call.id);
          break;
        case "market_history":
          response = await this.data.market(ctx, call.id, args);
          break;
        case "quant_calculate":
          response = await this.data.calculate(ctx, call.id, args);
          break;
        case "submit_specialist":
          if (args.researchContextRef)
            await this.research.loadContext(
              ctx.ownerId,
              args.researchContextRef,
            );
          if (task.input.trialId && args.role !== "prediction")
            throw new DomainError("trial_tool_not_allowed", 403);
          return await this.tasks.dispatchTool(
            ctx,
            call.id,
            args.role,
            args.requestKey,
            {
              input: args.question,
              datasetRefs: args.datasetRefs,
              ...(args.researchContextRef
                ? { researchContextRef: args.researchContextRef }
                : {}),
              ...(task.input.trialId
                ? { trialId: task.input.trialId, trialArm: task.input.trialArm }
                : {}),
              ...(args.portfolioSnapshotRef
                ? { portfolioSnapshotRef: args.portfolioSnapshotRef }
                : {}),
            },
          );
        case "save_research_summary":
          return await this.canvas.summary(ctx, call, args);
        case "canvas_upsert":
          return await this.canvas.upsert(ctx, call, args);
        case "canvas_patch":
          return await this.canvas.patch(ctx, call, args);
        case "canvas_remove":
          return await this.canvas.remove(ctx, call, args);
        default:
          throw new DomainError("unknown_tool", 400);
      }
      // A duplicate in-flight data request returns pending. Do not cache that
      // response forever: a later retry must recover the durable final receipt.
      if (
        response &&
        typeof response === "object" &&
        "status" in response &&
        ["running", "stopping", "unknown"].includes(String(response.status))
      )
        return response;
      return this.calls.finish(ctx, call.id, response);
    } catch (error) {
      if (
        name === "canvas_upsert" &&
        error instanceof DomainError &&
        error.code === "incompatible_result"
      ) {
        const row = (
          await this.pool.query(
            "SELECT kind FROM results WHERE id=$1 AND owner_id=$2",
            [args.resultRef, ctx.ownerId],
          )
        ).rows[0];
        const components: Record<string, string> = {
          indicators: "indicator_panel",
          prediction: "prediction_result",
          portfolio: "portfolio_summary",
          research_summary: "research_summary",
          forecast: "forecast_result",
          forecast_evaluation: "forecast_evaluation",
          review: "review",
        };
        if (row && components[row.kind])
          return this.calls.finish(ctx, call.id, {
            status: "needs_correction",
            reason: "incompatible_result",
            resultRef: args.resultRef,
            component: components[row.kind],
            instruction:
              "No artifact was saved. Retry canvas_upsert with this resultRef and component, using a new operation key. Task completion requires the corrected artifact.",
          });
      }
      // A storage/transport exception can mean the business COMMIT succeeded
      // but its acknowledgement was lost. Leave the call unresolved so the
      // same operation key can recover the module's durable business receipt.
      // Wrapped transient DomainErrors must retain the same recovery path.
      if (
        error instanceof DomainError &&
        // QuantRunner reports these only after its local subprocess exits;
        // the enclosing business transaction has rolled back before throwing.
        (/^quant_(?:timeout|output_too_large|input_failed|unavailable|failed|invalid_output)$/.test(
          error.code,
        ) ||
          (error.httpStatus < 500 &&
            !/(?:^|_)(?:unknown|unavailable|timeout|dependency|storage|database|connection)(?:_|$)/.test(
              error.code,
            )))
      )
        await this.calls.finish(ctx, call.id, { error: error.code });
      throw error;
    }
  }
}
export function createToolHandler(pool: Pool, data: DataService) {
  const gateway = new ToolGateway(pool, data);
  return async (req: Request) => {
    const json = (value: unknown, status = 200) =>
      Response.json(value, {
        status,
        headers: { "Cache-Control": "no-store" },
      });
    try {
      if (req.headers.has("Origin"))
        throw new DomainError("origin_not_allowed", 403);
      const token = /^Bearer ([a-f0-9]{64})$/.exec(
        req.headers.get("Authorization") ?? "",
      )?.[1];
      if (!token) throw new DomainError("unauthorized", 401);
      const ctx = await ToolCapabilities.authenticate(pool, token);
      const path = new URL(req.url).pathname;
      if (path === "/tools" && req.method === "GET")
        return json({ tools: await gateway.list(ctx) });
      if (path !== "/call" || req.method !== "POST")
        throw new DomainError("not_found", 404);
      if (!req.headers.get("Content-Type")?.startsWith("application/json"))
        throw new DomainError("invalid_content_type", 415);
      let size = 0;
      const chunks: Uint8Array[] = [];
      const reader = req.body?.getReader();
      if (!reader) throw new DomainError("invalid_request", 400);
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.length;
          if (size > 65536) throw new DomainError("request_too_large", 413);
          chunks.push(value);
        }
      } finally {
        await reader.cancel();
        reader.releaseLock();
      }
      let parsed: unknown;
      try {
        parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      } catch {
        throw new DomainError("invalid_json", 400);
      }
      const body = z
        .object({ tool: z.string(), arguments: z.unknown() })
        .strict()
        .parse(parsed);
      return json(
        await gateway.invoke(
          ctx,
          body.tool,
          body.arguments,
          req.headers.get("X-Alpha-Operation-Id") ?? "",
        ),
      );
    } catch (error) {
      if (error instanceof z.ZodError)
        return json({ error: "invalid_tool_arguments" }, 400);
      if (error instanceof DomainError)
        return json({ error: error.code }, error.httpStatus);
      return json({ error: "tool_dependency_unavailable" }, 503);
    }
  };
}
