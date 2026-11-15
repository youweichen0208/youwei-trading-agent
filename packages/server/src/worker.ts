import type { ToolCapabilities } from "./tool-context.js";
import { randomUUID } from "node:crypto";
import { DomainError, TaskStore, type Task } from "./tasks.js";
import { failureText } from "./failure-text.js";
import { fatalToolFailure } from "./tool-policy.js";
import {
  runSchema,
  UpstreamError,
  type RuntimeResolver,
  type Runtime,
  type Run,
} from "../../hermes-adapter/src/client.js";

const terminal = (status: string) =>
  ["completed", "failed", "cancelled", "interrupted"].includes(status);
const instructions = {
  research: `You are the research coordinator of a continuous US equity forecasting desk. Treat task input as the user's question plus server-selected references. Use only authorized project tools for market facts; never invent prices, results or specialist activity. Do not execute trades.
The coordinator can display saved specialist results: portfolio uses portfolio_summary, forward forecasts use forecast_result, retrospective predictions use prediction_result. forecast_evaluation is ONLY for an actual settled forecast evaluation. If canvas_upsert returns needs_correction, retry with its suggested component and the same resultRef under a new operation key before finishing.
For stock analysis use market_history, then quant_calculate(kind=indicators), and publish stock_chart and indicator_panel with canvas_upsert. Fetch enough history for the requested scope; the service handles indicator warmup. Resolve ambiguous symbols/dates by asking the user rather than guessing.
For personal stock research first read_research_context for the symbol, collect_evidence from available company disclosures, news, official IR/web, X and macro sources, then fetch the required split-adjusted market_history before read_research_context again to freeze the completed evidence AND dataset package. Use save_research_summary.researchContextRef for evidence-only summaries. Investigate both supporting and opposing evidence, management guidance versus prior promises, industry/peer transmission, valuation/market reaction and what expectations may already be priced in. Source failures are missing coverage, never evidence that no event occurred. Deduplicate retellings of the same event. A new stock borrows relevant company/industry/event lessons; never report borrowed cases as that stock's own track record. Answer once this bounded investigation is complete; before that communicate progress, not a provisional directional forecast. Explicitly report unresolved missing sources and evidence age.
When a forward prediction is requested, submit_specialist(role=prediction) once with ONLY the split-adjusted datasetRefs listed in the final frozen research context, and pass its researchContextRef both as a tool argument and in the question. Earlier datasets collected during this task may have been replaced in the snapshot; never pass the accumulated history of dataset references. Ask it to publish both 5- and 20-session forecasts and return their saved references. Bold, specific hypotheses are welcome; distinguish model judgments from observed facts. For explicitly retrospective statistics ask for the legacy quant prediction baseline instead. Read the user's portfolio relationships from research context. For an explicit portfolio calculation use the selected portfolioSnapshotRef (ask the user to select a snapshot if none was selected); ask if multiple portfolios are ambiguous. Portfolio datasetRefs must be raw prices for the holdings date; if only split-adjusted history is available, pass an empty datasetRefs array so the specialist fetches raw prices. At most one specialist per role. Use a stable requestKey for each dispatch. Do not copy private memories or unrelated conversation into specialist questions.
For a settled forecast review, read the exact forecast and outcome with read_forecasts, compare the original thesis against newly collected evidence, and record_review. Review successes as well as failures. Separate direction, size, timing and evidence supporting the proposed cause; an unexplained cause remains a hypothesis. Candidate lessons must specify scope, event type and horizon with supporting and opposing evidence. One case never proves a rule.
System research tasks include researchPhase and symbol. Execute only that phase: collect gathers and persists evidence; analyze freezes context and saves a concise thesis with opposing evidence, setting save_research_summary.forecastAction to retain/revise/renew and forecastIds to exact existing references; publish dispatches one prediction child for required horizons. Reuse saved job references rather than starting over. On daily checks, retain both unexpired forecasts unless material new evidence changes their thesis; renew an expired horizon even if direction is unchanged. Explain material changes explicitly before revising. During analyze, optionally include save_research_summary.trialCandidates (at most 3) only when a saved lesson version applies to an actual current evidence event; use the exact evidence.eventKey and lesson version_id from frozen context. The event must have a verified publication time after the lesson was proposed. Never manufacture event identities to increase sample counts. A background phase is not a user message.
Wait using get_specialist_result; read_result for the specialist's saved evidence. Never dispatch a second task to a role already dispatched, including after a partial result. Report that partial result with its reason instead. Save the final interpretation with save_research_summary and publish research_summary. Clearly label missing data and failed/partial work. Reuse artifactId to replace an existing component. Ordinary conversation without factual research needs no fabricated datasets or Canvas.`,
  prediction: `You are the forward prediction specialist. Never delegate or execute trades. Use the supplied frozen research context and authorized split-adjusted dataset references. read_research_context may retrieve a specific context; do not replace a supplied frozen snapshot with future material. Read prior forecasts before revising one. For each requested horizon (default both 5 and 20 sessions), publish_forecast with an explicit expected return, 80% return interval, probability of a strictly positive return, a concise thesis, opposing evidence and invalidation conditions. Evidence and adopted lesson version references must come from saved context. Values are subjective model judgments, never observed prices. The server fixes publication time and the first regular-session open after publication, then the fifth/twentieth session close; the starting price is not yet known, so forecast RETURNS, not invented future opening prices. Do not overwrite an earlier forecast: supersede it by reference and let both settle. Publish a forecast_result Canvas for each returned resultRef. Report actual evidence coverage and label initial probabilities as uncalibrated model judgments. Return all saved forecast and result references.
If explicitly asked for retrospective statistics, call quant_calculate(kind=prediction), read_result and canvas_upsert(component=prediction_result) for the legacy 20-session mean-return, 60-point MAE/RMSE and zero-return baseline. Keep that historical report distinct from the forward ledger. Never invent prices or metrics.
For an assigned shadow trial use its frozen evidence and specified method version only. Control uses currently adopted lessons; candidate adds the assigned candidate version. Do not read the other arm's output or any subsequently observed outcomes. Publish only the assigned arm with the trial tool; shadow output is not the user's primary forecast.`,
  portfolio: `You are the portfolio specialist. Never delegate, edit holdings or execute trades. read_holdings only the portfolioSnapshotRef selected by the user. Fetch raw daily prices with market_history(adjustment=raw) for that same completed holdings session, then quant_calculate(kind=portfolio) using those saved datasetRefs. A coordinator may supply split-adjusted research history: never use it as raw valuation quotes. If reusing a dataset, read_dataset and verify metadata.adjustment=raw and coverage of the exact holdings date first. read_result before publishing portfolio_summary. If inconsistent_price_basis occurs, fetch the correct raw data and recalculate within this same task; do not ask the coordinator to dispatch you again. Report genuinely missing quotes and null totals/weights as incomplete; do not treat missing prices as zero. Return the saved resultRef and label the portfolio simulated.`,
};

const languageDirective =
  "\nOutput language: write all reasoning, progress commentary and the final answer in Simplified Chinese. Keep tool names, parameter names, ticker symbols, ISO dates, JSON keys and numeric values exactly as specified; do not translate identifiers.";

function taskInstructions(task: Task) {
  if (task.input.trialId) {
    return (
      (task.role === "research"
        ? `You coordinate one isolated shadow experiment arm. Immediately submit_specialist(role=prediction,requestKey="trial-arm",datasetRefs=[],question="Complete only the assigned trial arm using get_trial_context and publish_forecast_trial_arm."). The server carries the assignment. Wait with get_specialist_result and return its receipt. Do not collect new evidence, inspect other research, publish a primary forecast, save a summary or use Canvas. This is a background experiment, not a user answer.`
        : `You are the prediction specialist for one isolated shadow experiment arm. Call get_trial_context with no arguments. Use only its frozen evidence and the assigned method/lesson version. Form a 5- or 20-session expected return, an 80% return interval, and probability of strictly positive return. Call publish_forecast_trial_arm using its trialId and arm, with four decimal-string distribution fields. Do not fetch fresh evidence, read the other arm, or publish a primary forecast. Return only the saved receipt. The server enforces the future opening deadline.`) +
      languageDirective
    );
  }
  if (task.role === "research" && task.input.researchPhase) {
    const phases: Record<string, string> = {
      collect: "Collect and persist available evidence and required split-adjusted market_history ending explicitly at marketEndSession. Optional source failures mean partial coverage; missing required prices means failure. Finally read_research_context to freeze evidence and datasetRefs. Return that contextRef. Do not calculate indicators, publish charts, analyze a thesis or dispatch specialists.",
      analyze: "Read the supplied frozen researchContextRef and datasetRefs; do not collect again. Save a research_summary with thesis, opposing evidence, missing coverage and forecastAction retain/revise/renew plus exact retained forecastIds. Retain only both unexpired horizons when evidence has not materially changed. Do not dispatch a specialist.",
      publish: "Read the saved summary and frozen researchContextRef. Submit exactly one prediction specialist for both 5 and 20 trading days using only datasetRefs from that context and pass researchContextRef. Wait with get_specialist_result; read and display saved forecast_result references. Never recollect or invent a completed forecast.",
      review: "Read the assigned forecast and evaluation, compare its thesis with evidence and record_review. Distinguish direction, magnitude, timing and uncertainty; one case does not prove a lesson.",
    };
    const phase = phases[String(task.input.researchPhase)];
    if (phase)
      return `You execute one background research phase using authorized tools only. Reuse saved references in input; never execute trades or invent facts. ${phase}${languageDirective}`;
  }
  return instructions[task.role] + languageDirective;
}

export class TaskWorker {
  readonly id = randomUUID();
  constructor(
    readonly store: TaskStore,
    private resolve: RuntimeResolver,
    private capabilities?: ToolCapabilities,
  ) {}
  async once(owner: string): Promise<boolean> {
    const task = await this.store.take(owner, this.id);
    if (!task) return false;
    try {
      await this.step(task);
    } catch (error) {
      if (error instanceof DomainError && error.code === "stale_lease")
        return true;
      if (error instanceof DomainError && error.code === "profile_starting") {
        const execution = await this.store.execution(task.owner_id, task.id);
        await this.defer(
          task,
          execution?.submission_started_at
            ? "unknown"
            : task.cancel_requested_at
              ? "stopping"
              : "running",
          execution?.submission_started_at ? "profile_unavailable" : error.code,
        );
        return true;
      }
      // Never turn a transport/storage failure into proof of upstream exit.
      const code =
        error instanceof UpstreamError || error instanceof DomainError
          ? error.code
          : "worker_dependency_unavailable";
      try {
        await this.defer(task, "unknown", code, 5000);
      } catch (fence) {
        if (!(fence instanceof DomainError && fence.code === "stale_lease"))
          throw fence;
      }
    }
    return true;
  }
  private defer(
    t: Task,
    status: "running" | "stopping" | "unknown",
    reason: string | null,
    delay = 1000,
  ) {
    return this.store.defer(
      t.owner_id,
      t.id,
      this.id,
      t.generation,
      status,
      reason,
      delay,
    );
  }
  private finish(
    t: Task,
    status: "completed" | "failed" | "cancelled" | "interrupted",
    output: Record<string, unknown>,
  ) {
    return this.store.finish(
      t.owner_id,
      t.id,
      this.id,
      t.generation,
      status,
      output,
    );
  }
  private async stopChildren(t: Task) {
    const children = await this.store.children(t.owner_id, t.id);
    for (const child of children)
      if (!terminal(child.status)) await this.store.stop(t.owner_id, child.id);
    return children.every((ch) => terminal(ch.status));
  }
  private async noExecution(t: Task, reason: string) {
    if (!(await this.stopChildren(t))) return this.defer(t, "stopping", reason);
    const latest = await this.store.get(t.owner_id, t.id);
    await this.finish(t, latest.cancel_requested_at ? "cancelled" : "failed", {
      reason: latest.cancel_reason ?? latest.failure_code ?? reason,
      text: latest.cancel_requested_at
        ? "任务已停止。"
        : "当前 Profile 尚未连接可验证的运行环境，任务未执行。",
    });
  }
  private async settle(t: Task, run: Run) {
    const operations = await this.store.operations(t.owner_id, t.id);
    if (
      operations.some((o) =>
        ["running", "stopping", "unknown"].includes(o.status),
      )
    )
      return this.defer(
        t,
        operations.some((o) => o.status === "unknown")
          ? "unknown"
          : t.cancel_requested_at
            ? "stopping"
            : "running",
        "waiting_for_tools",
      );
    const toolFailed = fatalToolFailure(operations) !== null;
    const children = await this.store.children(t.owner_id, t.id);
    const childFailed = children.some(
      (ch) => terminal(ch.status) && ch.status !== "completed",
    );
    const latest = await this.store.get(t.owner_id, t.id);
    const unsuccessful =
      run.status !== "completed" ||
      childFailed ||
      toolFailed ||
      !!latest.cancel_requested_at;
    if (unsuccessful && !(await this.stopChildren(t)))
      return this.defer(
        t,
        latest.cancel_requested_at ? "stopping" : "running",
        "waiting_for_children",
      );
    if (children.some((ch) => !terminal(ch.status)))
      return this.defer(t, "running", "waiting_for_children");
    const status = latest.cancel_requested_at
      ? "cancelled"
      : latest.failure_code || childFailed || toolFailed
        ? "failed"
        : run.status === "completed"
          ? run.output?.trim()
            ? "completed"
            : "interrupted"
          : run.status === "cancelled"
            ? "interrupted"
            : (run.status as "failed" | "interrupted");
    const reason =
      latest.cancel_reason ??
      latest.failure_code ??
      (toolFailed
        ? "required_tool_failed"
        : childFailed
          ? "required_child_failed"
          : run.status === "completed" && !run.output?.trim()
            ? "final_output_missing"
            : status);
    const resultRefs = [
      ...new Set([
        ...operations.flatMap((op) =>
          typeof op.result?.resultRef === "string" ? [op.result.resultRef] : [],
        ),
        ...children.flatMap((child) => {
          const output = child.output as { resultRefs?: unknown } | null;
          return Array.isArray(output?.resultRefs)
            ? output.resultRefs.filter(
                (ref): ref is string => typeof ref === "string",
              )
            : [];
        }),
      ]),
    ];
    const datasetRefs = operations.flatMap((op) =>
      typeof op.result?.datasetRef === "string" ? [op.result.datasetRef] : [],
    );
    try {
      await this.finish(t, status, {
        resultRefs,
        datasetRefs,
        text:
          status === "completed" ? run.output : failureText(status, reason),
        reason,
        upstreamStatus: run.status,
      });
    } catch (error) {
      if (
        error instanceof DomainError &&
        ["cancellation_pending", "failure_pending"].includes(error.code)
      )
        return this.defer(t, "stopping", null);
      throw error;
    }
  }
  private async collectEvents(
    t: Task,
    execution: NonNullable<Awaited<ReturnType<TaskStore["execution"]>>>,
    runtime: Runtime | undefined,
    ended: boolean,
  ) {
    if (execution.event_closed || !execution.upstream_run_id) return false;
    // No optional stream on test/custom runtimes. A missing previously bound
    // runtime after a durable terminal receipt is compensated by final output.
    if (runtime && !runtime.events) return false;
    let batch;
    try {
      if (!runtime?.events) throw new Error("event_runtime_unavailable");
      batch = await runtime.events(
        execution.upstream_run_id,
        Number(execution.event_cursor),
      );
    } catch {
      if (ended)
        batch = { events: [], gap: true, closed: true, limited: false };
    }
    if (!batch) return false;
    if (ended && !batch.closed && !batch.limited) {
      batch.gap = true;
      batch.closed = true;
    }
    await this.store.archiveEvents(
      t.owner_id,
      t.id,
      this.id,
      t.generation,
      execution.upstream_run_id,
      batch,
    );
    return batch.limited;
  }
  private async step(t: Task) {
    if (t.deadline.getTime() <= Date.now() && !t.cancel_requested_at) {
      await this.store.stop(t.owner_id, t.id, "deadline_exceeded");
      t = await this.store.get(t.owner_id, t.id);
    }
    const children = await this.store.children(t.owner_id, t.id);
    const operations = await this.store.operations(t.owner_id, t.id);
    const failedChildTask = children.find(
      (ch) => terminal(ch.status) && ch.status !== "completed",
    );
    const failedChild = !!failedChildTask;
    // Coverage tools may fail and be reported as missing evidence; only a
    // critical tool that never succeeded later makes the task unrecoverable.
    const failedTool = fatalToolFailure(operations);
    if (
      !t.cancel_requested_at &&
      !t.failure_code &&
      (failedChild || failedTool)
    ) {
      const roleLabel =
        failedChildTask?.role === "prediction"
          ? "预测"
          : failedChildTask?.role === "portfolio"
            ? "组合"
            : "专项";
      t = await this.store.requestFailure(
        t.owner_id,
        t.id,
        this.id,
        t.generation,
        failedChild ? "required_child_failed" : "required_tool_failed",
        failedChild
          ? `${roleLabel}任务${failedChildTask?.status === "cancelled" ? "被取消" : "未完成"}`
          : `${failedTool!.tool} 调用失败${failedTool!.errorCode ? `（${failedTool!.errorCode}）` : ""}`,
      );
    }
    if (t.failure_code) await this.stopChildren(t);
    let execution = await this.store.execution(t.owner_id, t.id);
    // This receipt was committed before waiting for children. It survives both
    // worker restarts and upstream terminal status retention expiry.
    if (execution?.terminal_receipt) {
      let candidate: Runtime | undefined;
      if (!execution.event_closed) {
        try {
          candidate = this.resolve(
            execution.profile_id,
            execution.runtime_identity,
          );
        } catch {
          // Archived exit evidence remains valid if registry loading fails.
        }
      }
      const runtime =
        candidate?.identity === execution.runtime_identity
          ? candidate
          : undefined;
      if (await this.collectEvents(t, execution, runtime, true))
        return this.defer(
          t,
          t.cancel_requested_at ? "stopping" : "running",
          null,
          0,
        );
      return this.settle(t, runSchema.parse(execution.terminal_receipt));
    }
    if (
      (t.cancel_requested_at || t.failure_code) &&
      !execution?.submission_started_at &&
      !execution?.upstream_run_id
    )
      return this.noExecution(t, "cancelled_before_submission");
    if (!execution) {
      const context = await this.store.executionContext(
        t.owner_id,
        t.id,
        this.id,
        t.generation,
      );
      if (!this.resolve(context.profileId))
        return this.noExecution(t, "profile_unavailable");
      // A recovered lease without a prepared execution proves no upstream call
      // was authorized. Restore running before preparing its first execution.
      if (t.status === "unknown") return this.defer(t, "running", null, 0);
      await this.store.prepareExecution(
        t.owner_id,
        t.id,
        this.id,
        t.generation,
        {
          input: JSON.stringify(t.input),
          session_id: context.sessionId,
          instructions: taskInstructions(t),
        },
        context.profileId,
        context.sessionId,
      );
      execution = (await this.store.execution(t.owner_id, t.id))!;
    }
    if (execution.upstream_run_id && !execution.runtime_identity)
      return this.defer(t, "unknown", "runtime_identity_missing", 5000);
    const runtime = this.resolve(
      execution.profile_id,
      execution.runtime_identity,
    );
    if (!runtime) {
      if (!execution.submission_started_at)
        return this.noExecution(t, "profile_unavailable");
      return this.defer(t, "unknown", "profile_unavailable", 5000);
    }
    if (
      execution.runtime_identity &&
      execution.runtime_identity !== runtime.identity
    )
      return this.defer(t, "unknown", "runtime_identity_changed", 5000);
    if (!execution.upstream_run_id) {
      // Leave margin for the bounded network request. An expired identity is
      // never retried, even using the same key: its ledger may have been purged.
      if (
        execution.submission_started_at &&
        Date.now() - execution.submission_started_at.getTime() >=
          runtime.retentionMs - 10_000
      )
        return this.defer(t, "unknown", "idempotency_window_expired", 30_000);
      if (!execution.submission_started_at && t.status === "unknown")
        return this.defer(t, "running", null, 0);
      const capability = await this.capabilities?.issue(
        t.owner_id,
        t.id,
        this.id,
        t.generation,
      );
      await this.store.beginSubmission(
        t.owner_id,
        t.id,
        this.id,
        t.generation,
        runtime.identity,
      );
      const id = await runtime.create(
        execution.request,
        execution.idempotency_key,
        capability,
      );
      await this.store.bindUpstream(
        t.owner_id,
        t.id,
        this.id,
        t.generation,
        id,
      );
      return this.defer(
        t,
        t.cancel_requested_at ? "stopping" : "running",
        null,
        0,
      );
    }
    const run = await runtime.get(
      execution.upstream_run_id,
      execution.session_id,
    );
    if (terminal(run.status)) {
      await this.store.recordReceipt(
        t.owner_id,
        t.id,
        this.id,
        t.generation,
        run,
      );
    }
    const moreEvents = await this.collectEvents(
      t,
      execution,
      runtime,
      terminal(run.status),
    );
    if (terminal(run.status)) {
      if (moreEvents)
        return this.defer(
          t,
          t.cancel_requested_at ? "stopping" : "running",
          null,
          0,
        );
      return this.settle(t, run);
    }
    if (run.status === "unknown")
      return this.defer(t, "unknown", "upstream_execution_unknown", 5000);
    if (run.status === "waiting_for_approval" || run.pending_controls?.length) {
      await this.store.requestFailure(
        t.owner_id,
        t.id,
        this.id,
        t.generation,
        "unsupported_interaction",
      );
      await this.stopChildren(t);
      await runtime.stop(run.run_id);
      return this.defer(t, "stopping", "unsupported_upstream_control");
    }
    // Re-read because stop may race with an upstream status request.
    const latest = await this.store.get(t.owner_id, t.id);
    if (latest.cancel_requested_at || latest.failure_code) {
      await runtime.stop(run.run_id);
      return this.defer(t, "stopping", null);
    }
    if (operations.some((op) => op.status === "unknown"))
      return this.defer(t, "unknown", "waiting_for_tools", 5000);
    // Re-claim as soon as the service scan comes around: the 1s idle gap that
    // used to sit between two upstream samples made streamed text arrive in
    // paragraphs. The lease is still released, so stop and state checks run.
    return this.defer(
      t,
      run.status === "stopping" ? "stopping" : "running",
      null,
      250,
    );
  }
}
