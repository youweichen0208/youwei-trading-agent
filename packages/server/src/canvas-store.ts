import { assertContextResources } from "./context-resources.js";
import {
  awaitTaskVerification,
  requireVerifiedTask,
} from "./task-verification.js";
import { z } from "zod";
import type { Pool, PoolClient } from "pg";
import { randomUUID } from "node:crypto";
import { DomainError, type Task } from "./tasks.js";
import type { ToolContext } from "./data-service.js";
import {
  assertToolWrite,
  canonical,
  toolTransaction,
  type ToolCall,
} from "./tool-context.js";
import { uuid } from "./business.js";

const title = z
  .string()
  .trim()
  .min(1)
  .max(120)
  .refine((s) => !/[<>\u0000-\u001f]/.test(s));
const props = z
  .object({
    color: z
      .string()
      .regex(/^#[a-fA-F0-9]{6}$/)
      .optional(),
    showVolume: z.boolean().optional(),
  })
  .strict();
const base = { artifactId: uuid.optional(), title, props: props.optional() };
export const canvasUpsert = z.discriminatedUnion("component", [
  z
    .object({ ...base, component: z.literal("stock_chart"), datasetRef: uuid })
    .strict(),
  ...[
    "indicator_panel",
    "research_summary",
    "prediction_result",
    "portfolio_summary",
    "forecast_result",
    "forecast_evaluation",
    "review",
  ].map((component) =>
    z
      .object({ ...base, component: z.literal(component), resultRef: uuid })
      .strict(),
  ),
]);
export const canvasPatch = z
  .object({
    artifactId: uuid,
    title: title.optional(),
    props: props.optional(),
  })
  .strict()
  .refine((v) => v.title !== undefined || v.props !== undefined);
export const canvasRemove = z.object({ artifactId: uuid }).strict();
export const summaryInput = z
  .object({
    text: z
      .string()
      .trim()
      .min(1)
      .max(16000)
      .refine((s) => !/[<>\u0000]/.test(s)),
    datasetRefs: z.array(uuid).max(20),
    resultRefs: z.array(uuid).max(20),
    forecastAction: z.enum(["retain", "revise", "renew"]).optional(),
    forecastIds: z.array(uuid).max(20).optional(),
    researchContextRef: uuid.optional(),
    trialCandidates: z
      .array(
        z
          .object({
            lessonVersionRef: uuid,
            eventKey: z.string().min(1).max(200),
          })
          .strict(),
      )
      .max(3)
      .optional(),
  })
  .strict()
  .refine(
    (v) => !v.trialCandidates?.length || !!v.researchContextRef,
    "trial_context_required",
  )
  .refine(
    (v) =>
      v.datasetRefs.length + v.resultRefs.length > 0 || !!v.researchContextRef,
  );
const expectedKind: Record<string, string> = {
  indicator_panel: "indicators",
  prediction_result: "prediction",
  portfolio_summary: "portfolio",
  forecast_result: "forecast",
  forecast_evaluation: "forecast_evaluation",
  review: "review",
  research_summary: "research_summary",
};
const roles: Record<string, string[]> = {
  research: [
    "portfolio_summary",
    "forecast_result",
    "prediction_result",
    "stock_chart",
    "indicator_panel",
    "research_summary",
    "forecast_evaluation",
    "review",
  ],
  prediction: ["prediction_result", "forecast_result"],
  portfolio: ["portfolio_summary"],
};

// Anthropic rejects top-level unions; Hermes would otherwise discard their
// properties. Advertise a plain object while retaining the exact discriminated
// union above as the authoritative validator for every incoming tool call.
export function canvasModelInput(role: string) {
  const components = roles[role];
  if (!components?.length) throw new Error("unknown_canvas_role");
  const hasDataset = components.includes("stock_chart");
  return z
    .object({
      ...base,
      component: z.enum(components),
      ...(hasDataset ? { datasetRef: uuid.optional() } : {}),
      resultRef: hasDataset ? uuid.optional() : uuid,
    })
    .strict();
}

export class CanvasStore {
  constructor(readonly pool: Pool) {}
  private once(
    ctx: ToolContext,
    call: ToolCall,
    fn: (c: PoolClient, t: Task) => Promise<unknown>,
  ) {
    return awaitTaskVerification(() =>
      toolTransaction(this.pool, ctx, async (c, t) => {
        const saved = (
          await c.query(
            "SELECT * FROM tool_calls WHERE id=$1 AND task_id=$2 AND owner_id=$3",
            [call.id, t.id, ctx.ownerId],
          )
        ).rows[0];
        if (!saved) throw new DomainError("not_found", 404);
        if (saved.response !== null) return saved.response;
        requireVerifiedTask(t);
        assertToolWrite(t);
        const response = await fn(c, t);
        await c.query("UPDATE tool_calls SET response=$2 WHERE id=$1", [
          call.id,
          JSON.stringify(response),
        ]);
        return response;
      }),
    );
  }
  async workspace(ctx: ToolContext) {
    return toolTransaction(this.pool, ctx, async (c, t) => {
      const workspace = (
        await c.query(
          "SELECT id,version,revision,snapshot FROM workspaces WHERE id=$1 AND owner_id=$2",
          [t.workspace_id, t.owner_id],
        )
      ).rows[0];
      if (workspace.revision === t.workspace_revision)
        return { ...workspace, archived: false };
      const artifacts = (
        await c.query(
          "SELECT body FROM task_artifacts WHERE root_id=$1 AND owner_id=$2 AND NOT removed ORDER BY array_position((SELECT artifact_order FROM tasks WHERE id=$1),id) NULLS LAST,id",
          [t.root_id, t.owner_id],
        )
      ).rows.map((r) => r.body);
      return {
        ...workspace,
        revision: t.workspace_revision,
        snapshot: { artifacts },
        archived: true,
      };
    });
  }
  async summary(ctx: ToolContext, call: ToolCall, input: unknown) {
    const request = summaryInput.parse(input);
    return this.once(ctx, call, async (c, t) => {
      if (t.role !== "research") throw new DomainError("tool_not_allowed", 403);
      for (const [table, ids] of [
        ["datasets", request.datasetRefs],
        ["results", request.resultRefs],
      ] as const) {
        await assertContextResources(c, t.owner_id, t.thread_id, table, ids);
      }
      if (request.forecastIds?.length) {
        const found = await c.query(
          "SELECT id FROM forecasts WHERE owner_id=$1 AND id=ANY($2::uuid[])",
          [t.owner_id, request.forecastIds],
        );
        if (found.rowCount !== new Set(request.forecastIds).size)
          throw new DomainError("not_found", 404);
      }
      if (request.researchContextRef) {
        const found = await c.query(
          `SELECT rc.id,rc.payload FROM research_contexts rc JOIN thread_contexts tc ON tc.owner_id=rc.owner_id AND tc.thread_id=$3
          JOIN execution_contexts ec ON ec.id=tc.context_id AND ec.kind='personal'
          WHERE rc.id=$1 AND rc.owner_id=$2`,
          [request.researchContextRef, t.owner_id, t.thread_id],
        );
        if (!found.rowCount) throw new DomainError("not_found", 404);
        const frozen = found.rows[0].payload;
        for (const candidate of request.trialCandidates ?? []) {
          const eligible = [
            ...(frozen.trialCandidates ?? []),
            ...(frozen.lessons ?? []),
          ].some(
            (lesson: any) => lesson.version_id === candidate.lessonVersionRef,
          );
          if (
            !eligible ||
            !frozen.evidence?.some(
              (e: any) => e.eventKey === candidate.eventKey,
            )
          )
            throw new DomainError("trial_candidate_outside_context", 422);
        }
      }
      const lineage = {
        datasetRefs: request.datasetRefs,
        resultRefs: request.resultRefs,
        codeVersion: "research-summary-v1",
        interpretation: true,
        ...(request.researchContextRef
          ? { researchContextRef: request.researchContextRef }
          : {}),
      };
      await c.query(
        "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage,gateway_call_id) VALUES($1,$2,$3,'research_summary',$4,$5,$1)",
        [
          call.id,
          t.owner_id,
          t.id,
          JSON.stringify({
            text: request.text,
            label: "研究解释",
            ...(request.forecastAction
              ? {
                  forecastAction: request.forecastAction,
                  forecastIds: request.forecastIds ?? [],
                }
              : {}),
            ...(request.trialCandidates
              ? { trialCandidates: request.trialCandidates }
              : {}),
          }),
          JSON.stringify(lineage),
        ],
      );
      return { resultRef: call.id };
    });
  }
  async upsert(ctx: ToolContext, call: ToolCall, input: unknown) {
    const request = canvasUpsert.parse(input);
    return this.once(ctx, call, async (c, t) => {
      if (!roles[t.role]?.includes(request.component))
        throw new DomainError("tool_not_allowed", 403);
      if (
        request.component !== "stock_chart" &&
        request.props?.showVolume !== undefined
      )
        throw new DomainError("invalid_canvas_props", 400);
      const id = request.artifactId ?? call.id;
      const existing = (
        await c.query("SELECT * FROM task_artifacts WHERE id=$1", [id])
      ).rows[0];
      if (
        existing &&
        (existing.owner_id !== t.owner_id || existing.root_id !== t.root_id)
      )
        throw new DomainError("not_found", 404);
      if (!existing || existing.removed) {
        const count = (
          await c.query(
            "SELECT count(*) FROM task_artifacts WHERE root_id=$1 AND NOT removed",
            [t.root_id],
          )
        ).rows[0];
        if (Number(count.count) >= 50)
          throw new DomainError("canvas_limit_exceeded", 429);
      }
      let identity: unknown;
      if ("datasetRef" in request) {
        const dataset = (
          await c.query(
            `SELECT d.metadata FROM datasets d JOIN tool_operations op ON op.id=d.operation_id
             JOIN tasks source ON source.id=op.task_id JOIN thread_contexts sc ON sc.thread_id=source.thread_id
             JOIN thread_contexts tc ON tc.context_id=sc.context_id AND tc.owner_id=sc.owner_id
             WHERE d.id=$1 AND d.owner_id=$2 AND tc.thread_id=$3`,
            [request.datasetRef, t.owner_id, t.thread_id],
          )
        ).rows[0];
        if (!dataset) throw new DomainError("not_found", 404);
        if (dataset.metadata.kind !== "ohlcv")
          throw new DomainError("incompatible_dataset", 422);
        identity = dataset.metadata;
      } else {
        const result = (
          await c.query(
            `SELECT r.kind,r.lineage FROM results r JOIN tasks source ON source.id=r.task_id
             JOIN thread_contexts sc ON sc.thread_id=source.thread_id
             JOIN thread_contexts tc ON tc.context_id=sc.context_id AND tc.owner_id=sc.owner_id
             WHERE r.id=$1 AND r.owner_id=$2 AND tc.thread_id=$3 AND (r.operation_id IS NOT NULL OR r.gateway_call_id IS NOT NULL
               OR (r.kind='forecast' AND EXISTS(SELECT 1 FROM forecasts f WHERE f.result_id=r.id AND f.owner_id=r.owner_id))
               OR (r.kind='forecast_evaluation' AND EXISTS(SELECT 1 FROM forecast_evaluations e WHERE e.result_id=r.id AND e.owner_id=r.owner_id))
               OR (r.kind='review' AND EXISTS(SELECT 1 FROM forecast_reviews v WHERE v.result_id=r.id AND v.owner_id=r.owner_id)))`,
            [request.resultRef, t.owner_id, t.thread_id],
          )
        ).rows[0];
        if (!result) throw new DomainError("not_found", 404);
        if (result.kind !== expectedKind[request.component])
          throw new DomainError("incompatible_result", 422);
        identity = result.lineage;
      }
      const body = {
        ...request,
        artifactId: id,
        props: request.props ?? {},
        identity,
      };
      if (
        existing &&
        !existing.removed &&
        canonical(existing.body) === canonical(body)
      )
        return { artifactId: id, changed: false };
      await c.query(
        `INSERT INTO task_artifacts(id,owner_id,root_id,source_task_id,body) VALUES($1,$2,$3,$4,$5)
        ON CONFLICT(id) DO UPDATE SET body=excluded.body,removed=false`,
        [id, t.owner_id, t.root_id, t.id, JSON.stringify(body)],
      );
      return this.publish(c, t, "canvas.upsert", body);
    });
  }
  async patch(ctx: ToolContext, call: ToolCall, input: unknown) {
    const request = canvasPatch.parse(input);
    return this.once(ctx, call, async (c, t) => {
      const old = (
        await c.query(
          "SELECT body FROM task_artifacts WHERE id=$1 AND root_id=$2 AND owner_id=$3 AND NOT removed",
          [request.artifactId, t.root_id, t.owner_id],
        )
      ).rows[0];
      if (!old) throw new DomainError("not_found", 404);
      if (!roles[t.role]?.includes(old.body.component))
        throw new DomainError("tool_not_allowed", 403);
      if (
        old.body.component !== "stock_chart" &&
        request.props?.showVolume !== undefined
      )
        throw new DomainError("invalid_canvas_props", 400);
      const body = {
        ...old.body,
        ...(request.title ? { title: request.title } : {}),
        ...(request.props ? { props: request.props } : {}),
      };
      if (canonical(body) === canonical(old.body))
        return { artifactId: request.artifactId, changed: false };
      await c.query("UPDATE task_artifacts SET body=$2 WHERE id=$1", [
        request.artifactId,
        JSON.stringify(body),
      ]);
      return this.publish(c, t, "canvas.patch", body);
    });
  }
  async remove(ctx: ToolContext, call: ToolCall, input: unknown) {
    const request = canvasRemove.parse(input);
    return this.once(ctx, call, async (c, t) => {
      const old = (
        await c.query(
          "SELECT body,removed FROM task_artifacts WHERE id=$1 AND root_id=$2 AND owner_id=$3",
          [request.artifactId, t.root_id, t.owner_id],
        )
      ).rows[0];
      if (!old) throw new DomainError("not_found", 404);
      if (!roles[t.role]?.includes(old.body.component))
        throw new DomainError("tool_not_allowed", 403);
      if (old.removed)
        return { artifactId: request.artifactId, changed: false };
      await c.query("UPDATE task_artifacts SET removed=true WHERE id=$1", [
        request.artifactId,
      ]);
      return this.publish(c, t, "canvas.remove", {
        artifactId: request.artifactId,
      });
    });
  }
  private async publish(
    c: PoolClient,
    t: Task,
    event: string,
    body: Record<string, unknown>,
  ) {
    const workspace = (
      await c.query(
        "SELECT * FROM workspaces WHERE id=$1 AND owner_id=$2 FOR UPDATE",
        [t.workspace_id, t.owner_id],
      )
    ).rows[0];
    const archived = workspace.revision !== t.workspace_revision;
    let version = workspace.version;
    if (!archived) {
      const artifacts: Record<string, unknown>[] = workspace.snapshot.artifacts;
      const index = artifacts.findIndex(
        (a) => a.artifactId === body.artifactId,
      );
      if (event === "canvas.remove") {
        if (index >= 0) artifacts.splice(index, 1);
      } else if (index >= 0) artifacts[index] = body;
      else artifacts.push(body);
      version = (
        await c.query(
          "UPDATE workspaces SET snapshot=$2,version=version+1 WHERE id=$1 RETURNING version",
          [workspace.id, JSON.stringify({ ...workspace.snapshot, artifacts })],
        )
      ).rows[0].version;
    }
    const rootOrder = (
      await c.query("SELECT artifact_order FROM tasks WHERE id=$1", [t.root_id])
    ).rows[0].artifact_order as string[];
    const order =
      event === "canvas.remove"
        ? rootOrder.filter((id) => id !== body.artifactId)
        : rootOrder.includes(String(body.artifactId))
          ? rootOrder
          : [...rootOrder, String(body.artifactId)];
    await c.query("UPDATE tasks SET artifact_order=$2 WHERE id=$1", [
      t.root_id,
      order,
    ]);
    const sequence = (
      await c.query(
        "UPDATE tasks SET sequence=sequence+1 WHERE id=$1 RETURNING sequence",
        [t.root_id],
      )
    ).rows[0].sequence;
    const data = {
      ...body,
      workspaceId: workspace.id,
      workspaceVersion: version,
      workspaceRevision: t.workspace_revision,
      archived,
      ...(t.parent_id
        ? { childTaskId: t.id, parentTaskId: t.parent_id, role: t.role }
        : {}),
    };
    await c.query(
      "INSERT INTO task_events(id,owner_id,task_id,sequence,event,data) VALUES($1,$2,$3,$4,$5,$6)",
      [
        randomUUID(),
        t.owner_id,
        t.root_id,
        sequence,
        event,
        JSON.stringify(data),
      ],
    );
    return {
      artifactId: body.artifactId,
      workspaceId: workspace.id,
      workspaceVersion: version,
      archived,
      changed: true,
    };
  }
}
