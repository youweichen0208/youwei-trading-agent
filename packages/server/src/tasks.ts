import {
  awaitTaskVerification,
  requireVerifiedTask,
} from "./task-verification.js";
import { createHash, randomUUID } from "node:crypto";
import type { Pool, PoolClient } from "pg";
import { transaction, lockOwner } from "./db.js";
import { toolDetail } from "./tool-summary.js";
import {
  criticalTools,
  fatalToolFailure,
  type ToolOperation,
} from "./tool-policy.js";
import type { EventBatch } from "../../hermes-adapter/src/events.js";

export type Role = "research" | "prediction" | "portfolio";
export type Status =
  | "queued"
  | "running"
  | "stopping"
  | "unknown"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";
export interface Task {
  id: string;
  owner_id: string;
  thread_id: string;
  workspace_id: string;
  workspace_revision: number;
  root_id: string;
  parent_id: string | null;
  role: Role;
  origin: "interactive" | "background";
  priority: number;
  budget_seconds: number;
  research_job_id: string | null;
  status: Status;
  generation: number;
  sequence: number;
  lease_owner: string | null;
  lease_until: Date | null;
  deadline: Date;
  input: Record<string, unknown>;
  request_hash: string;
  cancel_requested_at: Date | null;
  cancel_reason: string | null;
  failure_code: string | null;
  output: unknown;
  stream_text: string;
  stream_message_id: string;
}
export class DomainError extends Error {
  constructor(
    public code: string,
    public httpStatus = 409,
  ) {
    super(code);
  }
}
const terminal = (s: Status) =>
  ["completed", "failed", "cancelled", "interrupted"].includes(s);
function stable(v: unknown): string {
  if (v === null || typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) return `[${v.map(stable).join(",")}]`;
  return `{${Object.keys(v)
    .sort()
    .map(
      (k) =>
        `${JSON.stringify(k)}:${stable((v as Record<string, unknown>)[k])}`,
    )
    .join(",")}}`;
}
const digest = (v: unknown) =>
  createHash("sha256").update(stable(v)).digest("hex");

const unresolvedCanvasCorrection = `(response->>'status'='needs_correction' AND NOT EXISTS(
 SELECT 1 FROM task_artifacts a JOIN tasks source_task ON source_task.id=tool_calls.task_id
 WHERE a.owner_id=tool_calls.owner_id AND a.root_id=source_task.root_id AND NOT a.removed
 AND a.body->>'resultRef'=tool_calls.response->>'resultRef'
 AND a.body->>'component'=tool_calls.response->>'component'))`;

export class TaskStore {
  constructor(
    readonly pool: Pool,
    private readonly transactionClient?: PoolClient,
  ) {}
  private async owned<T>(
    owner: string,
    fn: (c: PoolClient) => Promise<T>,
  ): Promise<T> {
    const run = async (c: PoolClient) => {
      // All task mutations take this lock first, avoiding parent/child lock inversion.
      if (!(await lockOwner(c, owner))) throw new DomainError("not_found", 404);
      return fn(c);
    };
    return this.transactionClient
      ? run(this.transactionClient)
      : transaction(this.pool, run);
  }
  private async task(c: PoolClient, owner: string, id: string): Promise<Task> {
    const t = (
      await c.query<Task>("SELECT * FROM tasks WHERE id=$1 AND owner_id=$2", [
        id,
        owner,
      ])
    ).rows[0];
    if (!t) throw new DomainError("not_found", 404);
    return t;
  }
  private async event(c: PoolClient, t: Task, event: string, data: unknown) {
    const seq = (
      await c.query(
        "UPDATE tasks SET sequence=sequence+1 WHERE id=$1 RETURNING sequence",
        [t.root_id],
      )
    ).rows[0].sequence;
    await c.query(
      "INSERT INTO task_events(id,owner_id,task_id,sequence,event,data) VALUES($1,$2,$3,$4,$5,$6)",
      [
        randomUUID(),
        t.owner_id,
        t.root_id,
        seq,
        event,
        JSON.stringify({
          ...(data as object),
          ...(t.parent_id
            ? { childTaskId: t.id, parentTaskId: t.parent_id, role: t.role }
            : {}),
        }),
      ],
    );
  }
  async createThread(owner: string, contextId = owner) {
    return this.owned(owner, async (c) => {
      if (
        !(
          await c.query(
            "SELECT 1 FROM execution_contexts WHERE id=$1 AND owner_id=$2",
            [contextId, owner],
          )
        ).rowCount
      )
        throw new DomainError("not_found", 404);
      const id = randomUUID(),
        workspaceId = randomUUID();
      await c.query("INSERT INTO threads(id,owner_id) VALUES($1,$2)", [
        id,
        owner,
      ]);
      if (contextId !== owner)
        await c.query(
          "UPDATE thread_contexts SET context_id=$1 WHERE thread_id=$2 AND owner_id=$3",
          [contextId, id, owner],
        );
      await c.query(
        "INSERT INTO workspaces(id,owner_id,thread_id) VALUES($1,$2,$3)",
        [workspaceId, owner, id],
      );
      return { id, workspaceId };
    });
  }
  async submit(
    owner: string,
    threadId: string,
    key: string,
    input: Record<string, unknown>,
  ) {
    return this.submitTask(owner, threadId, key, input);
  }
  /** Internal scheduler admission. Background work never creates a user message. */
  async submitSystem(
    owner: string,
    key: string,
    input: Record<string, unknown>,
    options: {
      threadId?: string;
      priority: number;
      researchJobId?: string;
      budgetSeconds?: number;
    },
  ) {
    return this.owned(owner, async (c) => {
      const store = new TaskStore(this.pool, c);
      const old = (
        await c.query<Task>(
          "SELECT * FROM tasks WHERE owner_id=$1 AND request_key=$2",
          [owner, key],
        )
      ).rows[0];
      const threadId =
        old?.thread_id ??
        options.threadId ??
        (await store.createThread(owner)).id;
      if (!old && !options.threadId)
        await c.query(
          "UPDATE threads SET source='background',title=$2 WHERE id=$1",
          [threadId, String(input.input ?? "自动研究").slice(0, 40)],
        );
      const thread = (
        await c.query(
          "SELECT source FROM threads WHERE id=$1 AND owner_id=$2 AND deleted_at IS NULL",
          [threadId, owner],
        )
      ).rows[0];
      if (thread?.source !== "background")
        throw new DomainError("background_thread_required", 400);
      return store.submitTask(owner, threadId, key, input, {
        priority: options.priority,
        researchJobId: options.researchJobId,
        budgetSeconds: options.budgetSeconds ?? 120,
      });
    });
  }
  private async submitTask(
    owner: string,
    threadId: string,
    key: string,
    input: Record<string, unknown>,
    system?: {
      priority: number;
      researchJobId?: string;
      budgetSeconds: number;
    },
  ) {
    if (
      !/^[\x21-\x7e]{1,255}$/.test(key) ||
      typeof input.input !== "string" ||
      !input.input.trim() ||
      input.input.length > 16000
    )
      throw new DomainError("invalid_request", 400);
    return this.owned(owner, async (c) => {
      if (
        !(
          await c.query(
            "SELECT id FROM threads WHERE id=$1 AND owner_id=$2 AND deleted_at IS NULL",
            [threadId, owner],
          )
        ).rowCount
      )
        throw new DomainError("not_found", 404);
      if (
        system &&
        (!Number.isInteger(system.priority) ||
          system.priority < 0 ||
          system.priority >= 1000 ||
          !Number.isInteger(system.budgetSeconds) ||
          system.budgetSeconds < 10 ||
          system.budgetSeconds > 600)
      )
        throw new DomainError("invalid_background_budget", 400);
      const hash = digest({ threadId, input, ...(system ? { system } : {}) });
      const old = (
        await c.query<Task>(
          "SELECT * FROM tasks WHERE owner_id=$1 AND request_key=$2",
          [owner, key],
        )
      ).rows[0];
      if (old) {
        if (old.request_hash !== hash)
          throw new DomainError("idempotency_conflict");
        return old;
      }
      // The owner lock makes admission atomic across concurrent requests.
      // Unknown work still occupies capacity; replay of an existing key does not.
      const pending = await c.query(
        `SELECT 1 FROM tasks WHERE owner_id=$1 AND parent_id IS NULL AND origin='interactive'
         AND status IN ('queued','running','stopping','unknown') LIMIT 10`,
        [owner],
      );
      if (!system && pending.rowCount === 10)
        throw new DomainError("queue_full", 429);
      const workspace = (
        await c.query(
          `UPDATE workspaces SET revision=revision+1,version=version+1,snapshot='{"artifacts":[]}'
        WHERE thread_id=$1 AND owner_id=$2 RETURNING id,revision`,
          [threadId, owner],
        )
      ).rows[0];
      const id = randomUUID();
      const t = (
        await c.query<Task>(
          `INSERT INTO tasks(id,owner_id,thread_id,root_id,role,request_key,request_hash,input,workspace_id,workspace_revision,origin,priority,budget_seconds,research_job_id)
        VALUES($1,$2,$3,$1,'research',$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING *`,
          [
            id,
            owner,
            threadId,
            key,
            hash,
            JSON.stringify(input),
            workspace.id,
            workspace.revision,
            system ? "background" : "interactive",
            system?.priority ?? 1000,
            system?.budgetSeconds ?? 600,
            system?.researchJobId ?? null,
          ],
        )
      ).rows[0]!;
      if (!system)
        await c.query(
          "INSERT INTO messages(id,owner_id,thread_id,task_id,role,content) VALUES($1,$2,$3,$4,'user',$5)",
          [randomUUID(), owner, threadId, id, input.input],
        );
      await c.query(
        "UPDATE threads SET title=$3 WHERE id=$1 AND owner_id=$2 AND title='新研究'",
        [threadId, owner, (input.input as string).slice(0, 40)],
      );
      await this.event(c, t, "run.progress", { status: "queued" });
      return t;
    });
  }
  async get(owner: string, id: string) {
    const c = await this.pool.connect();
    try {
      return await this.task(c, owner, id);
    } finally {
      c.release();
    }
  }
  private async capacityAvailable(c: PoolClient, t: Task) {
    if (process.env.RESEARCH_SINGLE_ROOT !== "1" || t.status !== "queued")
      return true;
    await c.query(
      "SELECT pg_advisory_xact_lock(hashtext('research-root-capacity'))",
    );
    if ((await c.query("SELECT 1 FROM acceptance_capacity LIMIT 1")).rowCount)
      return false;
    const busy = await c.query(
      `SELECT 1 FROM tasks WHERE root_id<>$1 AND status IN ('running','stopping','unknown') LIMIT 1`,
      [t.root_id],
    );
    if (busy.rowCount) return false;
    // Containers with unconfirmed exits keep capacity, even after a task became terminal.
    const containers = await c.query(
      `SELECT 1 FROM profile_instances p WHERE p.lifecycle_managed AND p.state IN ('starting','ready','stopping','unknown')
      AND NOT EXISTS (SELECT 1 FROM profile_contexts pc JOIN thread_contexts tc ON tc.context_id=pc.context_id AND tc.owner_id=pc.owner_id
        WHERE pc.profile_id=p.id AND tc.thread_id=$1 AND p.owner_id=$2) LIMIT 1`,
      [t.thread_id, t.owner_id],
    );
    return !containers.rowCount;
  }
  async claim(
    owner: string,
    worker: string,
  ): Promise<{ task: Task; recovery: boolean } | null> {
    return this.owned(owner, async (c) => {
      // Expired active work is recovered before new admissions; never create a second run.
      let t = (
        await c.query<Task>(
          `SELECT * FROM tasks WHERE owner_id=$1 AND status IN ('running','stopping','unknown')
        AND lease_until < now() ORDER BY ordinal LIMIT 1`,
          [owner],
        )
      ).rows[0];
      const recovery = !!t;
      if (!t)
        t = (
          await c.query<Task>(
            `SELECT t.* FROM tasks t WHERE t.owner_id=$1 AND t.status='queued'
        AND NOT EXISTS(SELECT 1 FROM tasks active WHERE active.owner_id=t.owner_id AND active.role=t.role
          AND active.status IN ('running','stopping','unknown'))
        AND (t.parent_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM tasks earlier WHERE earlier.owner_id=t.owner_id
          AND earlier.parent_id IS NULL AND earlier.thread_id=t.thread_id AND earlier.ordinal<t.ordinal AND earlier.status NOT IN ('completed','failed','cancelled','interrupted')))
        ORDER BY t.priority DESC,t.ordinal LIMIT 1`,
            [owner],
          )
        ).rows[0];
      if (!t || !(await this.capacityAvailable(c, t))) return null;
      if (
        t.status === "queued" &&
        t.parent_id &&
        t.deadline.getTime() <= Date.now()
      ) {
        await c.query(
          "UPDATE tasks SET status='cancelled',error_code='deadline_exceeded',cancel_requested_at=now() WHERE id=$1",
          [t.id],
        );
        await this.event(c, t, "run.cancelled", {
          reason: "deadline_exceeded",
        });
        return null;
      }
      const status = recovery
        ? t.cancel_requested_at
          ? "stopping"
          : "unknown"
        : "running";
      const claimed = (
        await c.query<Task>(
          `UPDATE tasks SET status=$2,generation=generation+1,lease_owner=$3,
        deadline=CASE WHEN status='queued' AND parent_id IS NULL THEN now()+make_interval(secs=>budget_seconds) ELSE deadline END,
        lease_until=now()+interval '30 seconds' WHERE id=$1 RETURNING *`,
          [t.id, status, worker],
        )
      ).rows[0]!;
      await this.event(
        c,
        claimed,
        status === "unknown" ? "run.unknown" : "run.progress",
        { status, recovery },
      );
      return { task: claimed, recovery };
    });
  }
  private assertLease(t: Task, worker: string, generation: number) {
    if (
      t.lease_owner !== worker ||
      t.generation !== generation ||
      !t.lease_until ||
      t.lease_until.getTime() <= Date.now() ||
      terminal(t.status)
    )
      throw new DomainError("stale_lease");
  }
  async heartbeat(
    owner: string,
    id: string,
    worker: string,
    generation: number,
  ) {
    return this.owned(owner, async (c) => {
      const t = await this.task(c, owner, id);
      this.assertLease(t, worker, generation);
      await c.query(
        "UPDATE tasks SET lease_until=now()+interval '30 seconds' WHERE id=$1",
        [id],
      );
      return t;
    });
  }
  async prepareExecution(
    owner: string,
    id: string,
    worker: string,
    generation: number,
    request: unknown,
    profile: string,
    session: string,
  ) {
    return this.owned(owner, async (c) => {
      const t = await this.task(c, owner, id);
      this.assertLease(t, worker, generation);
      const existing = (
        await c.query("SELECT * FROM executions WHERE task_id=$1", [id])
      ).rows[0];
      if (existing) {
        if (
          digest(existing.request) !== digest(request) ||
          existing.profile_id !== profile ||
          existing.session_id !== session
        )
          throw new DomainError("execution_identity_conflict");
        return existing;
      }
      if (t.status !== "running" || t.cancel_requested_at)
        throw new DomainError("execution_not_admissible");
      return (
        await c.query(
          `INSERT INTO executions(task_id,idempotency_key,request,profile_id,session_id)
        VALUES($1,$2,$3,$4,$5) RETURNING *`,
          [id, randomUUID(), JSON.stringify(request), profile, session],
        )
      ).rows[0];
    });
  }
  async spawn(
    owner: string,
    parentId: string,
    worker: string,
    generation: number,
    role: Exclude<Role, "research">,
    key: string,
    input: Record<string, unknown>,
  ) {
    if (
      !["prediction", "portfolio"].includes(role) ||
      !/^[\x21-\x7e]{1,255}$/.test(key)
    )
      throw new DomainError("invalid_request", 400);
    return this.owned(owner, async (c) => {
      const parent = await this.task(c, owner, parentId);
      this.assertLease(parent, worker, generation);
      if (
        parent.role !== "research" ||
        parent.parent_id ||
        parent.status !== "running" ||
        parent.cancel_requested_at ||
        parent.deadline.getTime() <= Date.now()
      )
        throw new DomainError("dispatch_not_allowed");
      return this.spawnChild(c, parent, role, key, input);
    });
  }
  private async spawnChild(
    c: PoolClient,
    parent: Task,
    role: Exclude<Role, "research">,
    key: string,
    input: Record<string, unknown>,
  ) {
    const owner = parent.owner_id,
      parentId = parent.id;
    if (
      parent.failure_code ||
      (
        await c.query(
          "SELECT 1 FROM tool_operations WHERE task_id=$1 AND status='unknown'",
          [parentId],
        )
      ).rowCount
    )
      throw new DomainError("dispatch_not_allowed");
    const hash = digest({ role, input, key });
    const old = (
      await c.query<Task>(
        "SELECT * FROM tasks WHERE parent_id=$1 AND role=$2",
        [parentId, role],
      )
    ).rows[0];
    if (old) {
      if (old.request_hash !== hash)
        throw new DomainError("specialist_conflict");
      return old;
    }
    const id = randomUUID();
    const child = (
      await c.query<Task>(
        `INSERT INTO tasks(id,owner_id,thread_id,parent_id,root_id,role,request_key,request_hash,input,deadline,workspace_id,workspace_revision,origin,priority,budget_seconds,research_job_id)
        VALUES($1,$2,$3,$4,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15) RETURNING *`,
        [
          id,
          owner,
          parent.thread_id,
          parentId,
          role,
          `child:${parentId}:${role}`,
          hash,
          JSON.stringify(input),
          parent.deadline,
          parent.workspace_id,
          parent.workspace_revision,
          parent.origin,
          parent.priority,
          parent.budget_seconds,
          parent.research_job_id,
        ],
      )
    ).rows[0]!;
    await this.event(c, child, "run.progress", { status: "queued" });
    return child;
  }
  async dispatchTool(
    ctx: { ownerId: string; taskId: string; profileId: string },
    callId: string,
    role: Exclude<Role, "research">,
    key: string,
    input: Record<string, unknown>,
  ) {
    return awaitTaskVerification(() =>
      this.owned(ctx.ownerId, async (c) => {
        const parent = await this.task(c, ctx.ownerId, ctx.taskId);
        const call = (
          await c.query(
            "SELECT response FROM tool_calls WHERE id=$1 AND owner_id=$2 AND task_id=$3",
            [callId, ctx.ownerId, ctx.taskId],
          )
        ).rows[0];
        if (!call) throw new DomainError("not_found", 404);
        if (call.response !== null) return call.response;
        if (
          !(
            await c.query(
              "SELECT 1 FROM executions WHERE task_id=$1 AND profile_id=$2 AND submission_started_at IS NOT NULL",
              [parent.id, ctx.profileId],
            )
          ).rowCount
        )
          throw new DomainError("not_found", 404);
        requireVerifiedTask(parent);
        if (
          parent.role !== "research" ||
          parent.parent_id ||
          parent.status !== "running" ||
          parent.cancel_requested_at ||
          parent.deadline.getTime() <= Date.now()
        )
          throw new DomainError("dispatch_not_allowed");
        const refs = input.datasetRefs as string[];
        if (
          (
            await c.query(
              `SELECT d.id FROM datasets d JOIN tool_operations op ON op.id=d.operation_id
               JOIN tasks source ON source.id=op.task_id JOIN thread_contexts sc ON sc.thread_id=source.thread_id
               JOIN thread_contexts target ON target.context_id=sc.context_id AND target.owner_id=sc.owner_id
               WHERE d.owner_id=$1 AND d.id=ANY($2::uuid[]) AND target.thread_id=$3`,
              [ctx.ownerId, refs, parent.thread_id],
            )
          ).rowCount !== new Set(refs).size
        )
          throw new DomainError("not_found", 404);
        if (
          input.portfolioSnapshotRef &&
          input.portfolioSnapshotRef !== parent.input.portfolioSnapshotRef
        )
          throw new DomainError("portfolio_not_selected", 403);
        const child = await this.spawnChild(c, parent, role, key, input);
        const response = { childTaskId: child.id, status: child.status };
        await c.query("UPDATE tool_calls SET response=$2 WHERE id=$1", [
          callId,
          JSON.stringify(response),
        ]);
        return response;
      }),
    );
  }
  async stop(owner: string, id: string, reason = "user_requested") {
    return this.owned(owner, async (c) => {
      const parent = await this.task(c, owner, id);
      if (terminal(parent.status)) return parent;
      const tasks = (
        await c.query<Task>(
          "SELECT * FROM tasks WHERE id=$1 OR parent_id=$1 ORDER BY ordinal",
          [id],
        )
      ).rows;
      for (const t of tasks) {
        if (terminal(t.status)) continue;
        const status =
          t.status === "queued"
            ? "cancelled"
            : t.status === "unknown"
              ? "unknown"
              : "stopping";
        await c.query(
          "UPDATE tasks SET status=$2,cancel_requested_at=coalesce(cancel_requested_at,now()),cancel_reason=coalesce(cancel_reason,$3) WHERE id=$1",
          [t.id, status, reason],
        );
        await c.query(
          "UPDATE tool_operations SET status='stopping' WHERE task_id=$1 AND status='running'",
          [t.id],
        );
        await this.event(
          c,
          t,
          status === "cancelled"
            ? "run.cancelled"
            : status === "unknown"
              ? "run.unknown"
              : "run.progress",
          { status, reason },
        );
      }
      return this.task(c, owner, id);
    });
  }
  async finish(
    owner: string,
    id: string,
    worker: string,
    generation: number,
    status: "completed" | "failed" | "cancelled" | "interrupted",
    output: Record<string, unknown>,
  ) {
    return this.owned(owner, async (c) => {
      const t = await this.task(c, owner, id);
      this.assertLease(t, worker, generation);
      const children = (
        await c.query<Task>("SELECT * FROM tasks WHERE parent_id=$1", [id])
      ).rows;
      if (children.some((ch) => !terminal(ch.status)))
        throw new DomainError("children_still_running");
      if (t.failure_code && status === "completed")
        throw new DomainError("failure_pending");
      if (t.cancel_requested_at && status === "completed")
        throw new DomainError("cancellation_pending");
      if (
        status === "completed" &&
        children.some((ch) => ch.status !== "completed")
      )
        throw new DomainError("required_child_failed");
      if (status === "cancelled" && !t.cancel_requested_at)
        throw new DomainError("cancel_not_requested");
      const operations = (
        await c.query<ToolOperation>(
          `SELECT CASE kind WHEN 'market' THEN 'market_history' ELSE 'quant_calculate' END AS tool,
           status,error_code,coalesce(finished_at,started_at) AS at
           FROM tool_operations WHERE task_id=$1`,
          [id],
        )
      ).rows;
      if (
        operations.some((o) =>
          ["running", "stopping", "unknown"].includes(o.status),
        )
      )
        throw new DomainError("tools_still_running");
      if (status === "completed" && fatalToolFailure(operations))
        throw new DomainError("required_tool_failed");
      if (
        (
          await c.query(
            "SELECT 1 FROM tool_calls WHERE task_id=$1 AND response IS NULL",
            [id],
          )
        ).rowCount
      )
        throw new DomainError("tools_still_running");
      if (
        status === "completed" &&
        (
          await c.query(
            `SELECT 1 FROM tool_calls WHERE task_id=$1 AND (
              (${unresolvedCanvasCorrection})
              OR (
                response ? 'error' AND tool = ANY($2::text[])
                AND NOT EXISTS (
                  SELECT 1 FROM tool_calls ok
                  WHERE ok.task_id=tool_calls.task_id AND ok.owner_id=tool_calls.owner_id
                    AND ok.tool=tool_calls.tool AND ok.created_at>=tool_calls.created_at
                    AND ok.response IS NOT NULL AND NOT (ok.response ? 'error')
                )
              )
            )`,
            [id, [...criticalTools]],
          )
        ).rowCount
      )
        throw new DomainError("required_tool_failed");
      // Caller must provide upstream exit evidence; this method only fences the DB commit.
      await c.query(
        "UPDATE tasks SET status=$2,output=$3,lease_until=NULL WHERE id=$1",
        [id, status, JSON.stringify(output)],
      );
      // All tools and children have exited. Retire the capability in the same
      // transaction as the terminal state, without changing execution identity.
      await c.query(
        "UPDATE executions SET tool_token_hash=NULL,tool_token_ciphertext=NULL WHERE task_id=$1",
        [id],
      );
      if (
        !t.parent_id &&
        t.origin !== "background" &&
        typeof output.text === "string"
      )
        await c.query(
          `INSERT INTO messages(id,owner_id,thread_id,task_id,role,content)
        VALUES($1,$2,$3,$4,'assistant',$5)`,
          [t.stream_message_id, owner, t.thread_id, id, output.text],
        );
      await this.event(c, t, `run.${status}`, { status, output });
      return this.task(c, owner, id);
    });
  }
  async snapshot(owner: string, id: string) {
    return transaction(this.pool, async (c) => {
      await c.query(
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
      );
      const task = await this.task(c, owner, id);
      if (task.parent_id) throw new DomainError("root_task_required", 400);
      const children = (
        await c.query(
          "SELECT id,role,status,output FROM tasks WHERE parent_id=$1 AND owner_id=$2 ORDER BY ordinal",
          [id, owner],
        )
      ).rows;
      const messages = (
        await c.query(
          `SELECT m.id,m.role,m.content,m.task_id FROM messages m JOIN tasks t ON t.id=m.task_id
        WHERE m.thread_id=$1 AND m.owner_id=$2 ORDER BY t.ordinal,CASE m.role WHEN 'user' THEN 0 ELSE 1 END`,
          [task.thread_id, owner],
        )
      ).rows;
      const workspace = (
        await c.query(
          "SELECT id,version,revision,snapshot FROM workspaces WHERE thread_id=$1 AND owner_id=$2",
          [task.thread_id, owner],
        )
      ).rows[0];
      const sequence = (
        await c.query("SELECT sequence FROM tasks WHERE id=$1", [id])
      ).rows[0].sequence as number;
      const artifacts = (
        await c.query(
          "SELECT body FROM task_artifacts WHERE root_id=$1 AND owner_id=$2 AND NOT removed ORDER BY array_position((SELECT artifact_order FROM tasks WHERE id=$1),id) NULLS LAST,id",
          [id, owner],
        )
      ).rows.map((r) => r.body);
      const progress = (await c.query(
        `WITH steps AS (
           SELECT sequence, data FROM task_events
           WHERE task_id=$1 AND owner_id=$2 AND event IN ('run.progress','run.unknown')
             AND COALESCE(data->>'stage','') NOT IN ('reasoning.available','reasoning.delta','message.interim')
           ORDER BY sequence DESC LIMIT 100
         ), thoughts AS (
           SELECT sequence, data FROM task_events
           WHERE task_id=$1 AND owner_id=$2 AND event IN ('run.progress','run.unknown')
             AND data->>'stage' IN ('reasoning.available','reasoning.delta','message.interim')
           ORDER BY sequence DESC LIMIT 200
         )
         SELECT sequence,jsonb_build_object('stage',data->>'stage','tool',data->>'tool','status',data->>'status','role',data->>'role','detail',data->>'detail','reason',data->>'reason') AS data
         FROM (SELECT * FROM steps UNION ALL SELECT * FROM thoughts) merged
         ORDER BY sequence DESC`,
        [id, owner],
      )).rows.reverse();
      return {
        task,
        children,
        progress,
        messages,
        workspace,
        artifacts,
        lastSequence: sequence,
      };
    });
  }
  async archiveEvents(
    owner: string,
    id: string,
    worker: string,
    generation: number,
    runId: string,
    batch: EventBatch,
  ) {
    return this.owned(owner, async (c) => {
      const t = await this.task(c, owner, id);
      this.assertLease(t, worker, generation);
      const e = (
        await c.query("SELECT * FROM executions WHERE task_id=$1", [id])
      ).rows[0];
      if (!e || e.upstream_run_id !== runId)
        throw new DomainError("upstream_identity_mismatch");
      let cursor = Number(e.event_cursor),
        gap = e.event_gap || batch.gap;
      let text = t.stream_text;
      for (const event of batch.events) {
        if (!Number.isSafeInteger(event.sequence) || event.sequence < 0)
          throw new DomainError("invalid_event_cursor");
        if (event.sequence <= cursor) continue;
        if (event.sequence !== cursor + 1) gap = true;
        cursor = event.sequence;
        if (
          event.kind === "message.delta" &&
          !t.parent_id &&
          !gap &&
          event.text
        ) {
          if (text.length + event.text.length > 500_000) {
            gap = true;
            continue;
          }
          text += event.text;
          await this.event(c, t, "assistant.delta", {
            messageId: t.stream_message_id,
            text: event.text,
          });
        } else if (
          [
            "message.interim",
            "reasoning.available",
            "reasoning.delta",
          ].includes(event.kind) &&
          event.text
        ) {
          // Visible mid-turn commentary or provider reasoning already bounded
          // and redacted by the runtime; never raw private context.
          await this.event(c, t, "run.progress", {
            role: t.role,
            stage: event.kind,
            detail: event.text.slice(0, event.kind === "reasoning.delta" ? 2000 : 600),
          });
        } else if (
          ["tool.started", "tool.completed"].includes(event.kind) &&
          event.tool
        ) {
          const detail = toolDetail(
            event.kind,
            event.preview,
            event.durationSeconds,
          );
          await this.event(c, t, "run.progress", {
            role: t.role,
            stage: event.kind,
            tool: event.tool,
            ...(detail ? { detail } : {}),
          });
        }
      }
      if (gap && !e.event_gap)
        await this.event(c, t, "run.progress", {
          role: t.role,
          stage: "event_history_gap",
        });
      await c.query("UPDATE tasks SET stream_text=$2 WHERE id=$1", [id, text]);
      await c.query(
        `UPDATE executions SET event_cursor=$2,event_gap=$3,event_closed=$4 WHERE task_id=$1`,
        [id, cursor, gap, e.event_closed || batch.closed],
      );
    });
  }
  async bindUpstream(
    owner: string,
    id: string,
    worker: string,
    generation: number,
    runId: string,
  ) {
    return this.owned(owner, async (c) => {
      const t = await this.task(c, owner, id);
      this.assertLease(t, worker, generation);
      const execution = (
        await c.query("SELECT * FROM executions WHERE task_id=$1", [id])
      ).rows[0];
      if (!execution) throw new DomainError("execution_not_prepared");
      if (execution.upstream_run_id && execution.upstream_run_id !== runId)
        throw new DomainError("upstream_identity_conflict");
      await c.query(
        "UPDATE executions SET upstream_run_id=$2 WHERE task_id=$1",
        [id, runId],
      );
    });
  }
  // Each step yields its lease while waiting for upstream/children, so parents
  // cannot occupy all worker slots and prevent their specialists from running.
  async take(owner: string, worker: string): Promise<Task | null> {
    return this.owned(owner, async (c) => {
      const t = (
        await c.query<Task>(
          `
        SELECT t.* FROM tasks t WHERE owner_id=$1
        AND status IN ('queued','running','stopping','unknown')
        AND next_attempt_at<=now() AND (lease_until IS NULL OR lease_until<now())
        AND (status<>'queued' OR (
          NOT EXISTS(SELECT 1 FROM tasks a WHERE a.owner_id=t.owner_id AND a.role=t.role
            AND a.status IN ('running','stopping','unknown'))
          AND (parent_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM tasks a
            WHERE a.owner_id=t.owner_id AND a.parent_id IS NULL AND a.thread_id=t.thread_id AND a.ordinal<t.ordinal
            AND a.status NOT IN ('completed','failed','cancelled','interrupted')))))
        ORDER BY CASE WHEN status IN ('unknown','stopping') OR (status='running' AND lease_until IS NOT NULL) THEN 0
          WHEN status='queued' AND parent_id IS NULL THEN 2 ELSE 1 END,
          CASE WHEN status='queued' AND parent_id IS NULL THEN priority ELSE 0 END DESC,next_attempt_at,ordinal LIMIT 1`,
          [owner],
        )
      ).rows[0];
      if (!t || !(await this.capacityAvailable(c, t))) return null;
      const status =
        t.status === "queued"
          ? "running"
          : t.lease_until && !t.cancel_requested_at
            ? "unknown"
            : t.status;
      const claimed = (
        await c.query<Task>(
          `UPDATE tasks SET status=$2,
        deadline=CASE WHEN status='queued' AND parent_id IS NULL THEN now()+make_interval(secs=>budget_seconds) WHEN status='queued' THEN (SELECT parent.deadline FROM tasks parent WHERE parent.id=tasks.parent_id) ELSE deadline END,
        generation=generation+1,lease_owner=$3,lease_until=now()+interval '30 seconds'
        WHERE id=$1 RETURNING *`,
          [t.id, status, worker],
        )
      ).rows[0]!;
      if (status !== t.status)
        await this.event(
          c,
          claimed,
          status === "unknown" ? "run.unknown" : "run.progress",
          { status },
        );
      return claimed;
    });
  }
  async defer(
    owner: string,
    id: string,
    worker: string,
    generation: number,
    status: "running" | "stopping" | "unknown",
    reason: string | null,
    delayMs = 1000,
  ) {
    return this.owned(owner, async (c) => {
      const t = await this.task(c, owner, id);
      this.assertLease(t, worker, generation);
      // A stop that raced with the poll must not be overwritten by running.
      if ((t.cancel_requested_at || t.failure_code) && status === "running")
        status = "stopping";
      await c.query(
        `UPDATE tasks SET status=$2,error_code=$3,lease_owner=NULL,lease_until=NULL,
        next_attempt_at=now()+($4 * interval '1 millisecond') WHERE id=$1`,
        [id, status, reason, delayMs],
      );
      const previous = (t as Task & { error_code: string | null }).error_code;
      if (t.status !== status || previous !== reason)
        await this.event(
          c,
          t,
          status === "unknown" ? "run.unknown" : "run.progress",
          { status, reason },
        );
    });
  }
  async execution(owner: string, id: string) {
    await this.get(owner, id);
    return (
      await this.pool.query("SELECT * FROM executions WHERE task_id=$1", [id])
    ).rows[0] as
      | {
          task_id: string;
          idempotency_key: string;
          request: { input: string; session_id: string; instructions: string };
          profile_id: string;
          session_id: string;
          runtime_identity: string | null;
          runtime_container_id: string | null;
          runtime_container_started_at: string | null;
          runtime_deployment_hash: string | null;
          upstream_run_id: string | null;
          submission_started_at: Date | null;
          prepared_at: Date;
          terminal_receipt: Record<string, unknown> | null;
          event_cursor: string;
          event_gap: boolean;
          event_closed: boolean;
        }
      | undefined;
  }
  async executionContext(
    owner: string,
    id: string,
    worker: string,
    generation: number,
  ) {
    return this.owned(owner, async (c) => {
      const t = await this.task(c, owner, id);
      this.assertLease(t, worker, generation);
      const profile = (
        await c.query(
          `SELECT p.id FROM profile_instances p JOIN profile_contexts pc ON pc.profile_id=p.id AND pc.owner_id=p.owner_id
           JOIN thread_contexts tc ON tc.context_id=pc.context_id AND tc.owner_id=pc.owner_id
           WHERE p.owner_id=$1 AND p.role=$2 AND tc.thread_id=$3`,
          [owner, t.role, t.thread_id],
        )
      ).rows[0];
      if (!profile) throw new DomainError("profile_unavailable", 503);
      await c.query(
        `INSERT INTO profile_sessions(owner_id,thread_id,profile_id,hermes_session_id)
        VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING`,
        [owner, t.thread_id, profile.id, randomUUID()],
      );
      const session = (
        await c.query(
          "SELECT hermes_session_id FROM profile_sessions WHERE owner_id=$1 AND thread_id=$2 AND profile_id=$3",
          [owner, t.thread_id, profile.id],
        )
      ).rows[0];
      return {
        profileId: profile.id as string,
        sessionId: session.hermes_session_id as string,
      };
    });
  }
  async beginSubmission(
    owner: string,
    id: string,
    worker: string,
    generation: number,
    runtimeIdentity: string,
  ) {
    return this.owned(owner, async (c) => {
      const t = await this.task(c, owner, id);
      this.assertLease(t, worker, generation);
      const e = (
        await c.query("SELECT * FROM executions WHERE task_id=$1", [id])
      ).rows[0];
      if (!e) throw new DomainError("execution_not_prepared");
      if (e.runtime_identity && e.runtime_identity !== runtimeIdentity)
        throw new DomainError("runtime_identity_changed");
      const profile = (
        await c.query(
          "SELECT lifecycle_managed,state,container_id,container_started_at,deployment_hash FROM profile_instances WHERE id::text=$1 AND owner_id=$2",
          [e.profile_id, owner],
        )
      ).rows[0];
      if (
        profile?.lifecycle_managed &&
        (profile.state !== "ready" ||
          !profile.container_id ||
          !profile.container_started_at ||
          !profile.deployment_hash)
      )
        throw new DomainError("profile_starting");
      if (
        !e.submission_started_at &&
        (t.cancel_requested_at ||
          t.failure_code ||
          t.deadline.getTime() <= Date.now())
      )
        throw new DomainError("execution_not_admissible");
      await c.query(
        `UPDATE executions SET runtime_identity=$2,
        submission_started_at=coalesce(submission_started_at,now()),
        runtime_container_id=CASE WHEN submission_started_at IS NULL THEN $3 ELSE runtime_container_id END,
        runtime_container_started_at=CASE WHEN submission_started_at IS NULL THEN $4 ELSE runtime_container_started_at END,
        runtime_deployment_hash=CASE WHEN submission_started_at IS NULL THEN $5 ELSE runtime_deployment_hash END
        WHERE task_id=$1`,
        [
          id,
          runtimeIdentity,
          profile?.lifecycle_managed ? profile.container_id : null,
          profile?.lifecycle_managed ? profile.container_started_at : null,
          profile?.lifecycle_managed ? profile.deployment_hash : null,
        ],
      );
    });
  }
  async recordReceipt(
    owner: string,
    id: string,
    worker: string,
    generation: number,
    receipt: Record<string, unknown>,
  ) {
    return this.owned(owner, async (c) => {
      const t = await this.task(c, owner, id);
      this.assertLease(t, worker, generation);
      await c.query(
        "UPDATE executions SET terminal_receipt=coalesce(terminal_receipt,$2::jsonb) WHERE task_id=$1",
        [id, JSON.stringify(receipt)],
      );
    });
  }
  async children(owner: string, id: string): Promise<Task[]> {
    await this.get(owner, id);
    return (
      await this.pool.query<Task>(
        "SELECT * FROM tasks WHERE parent_id=$1 AND owner_id=$2 ORDER BY ordinal",
        [id, owner],
      )
    ).rows;
  }
  async requestFailure(
    owner: string,
    id: string,
    worker: string,
    generation: number,
    reason: string,
    detail?: string,
  ) {
    return this.owned(owner, async (c) => {
      const t = await this.task(c, owner, id);
      this.assertLease(t, worker, generation);
      await c.query(
        "UPDATE tasks SET failure_code=coalesce(failure_code,$2),status=CASE WHEN status='unknown' THEN 'unknown' ELSE 'stopping' END WHERE id=$1",
        [id, reason],
      );
      await c.query(
        "UPDATE tool_operations SET status='stopping' WHERE task_id=$1 AND status='running'",
        [id],
      );
      if (!t.failure_code)
        await this.event(c, t, "run.progress", {
          status: "stopping",
          reason,
          ...(detail ? { detail } : {}),
        });
      return this.task(c, owner, id);
    });
  }
  async operations(owner: string, id: string) {
    return this.owned(owner, async (c) => {
      await this.task(c, owner, id);
      // Recover a gateway crash after the data transaction committed but before
      // the HTTP-call receipt was saved. The durable data receipt is the proof.
      await c.query(
        `UPDATE tool_calls call SET response=jsonb_build_object('id',op.id,'status',op.status,'result',op.result,'error_code',op.error_code)
        FROM tool_operations op WHERE call.task_id=$1 AND call.owner_id=$2 AND call.response IS NULL
        AND op.task_id=call.task_id AND op.owner_id=call.owner_id AND op.operation_key=call.id::text
        AND op.status IN ('completed','failed','cancelled')`,
        [id, owner],
      );
      // The tool executor has a 25 s budget. A lost process is not permission to
      // repeat its operation: retain the identity and require reconciliation.
      await c.query(
        `UPDATE tool_operations SET status='unknown',error_code='tool_execution_unknown'
        WHERE task_id=$1 AND status IN ('running','stopping') AND started_at<now()-interval '45 seconds'`,
        [id],
      );
      return (
        await c.query<
          ToolOperation & {
            id: string;
            result: Record<string, unknown> | null;
          }
        >(
          `SELECT id,status,error_code,result,tool,at FROM (
            SELECT o.id,o.status,o.error_code,o.result,
              CASE o.kind WHEN 'market' THEN 'market_history' ELSE 'quant_calculate' END AS tool,
              coalesce(o.finished_at,o.started_at) AS at
            FROM tool_operations o WHERE o.task_id=$1 AND o.owner_id=$2
            UNION ALL
            SELECT tool_calls.id,
              CASE WHEN response IS NULL THEN CASE WHEN created_at<now()-interval '45 seconds' THEN 'unknown' ELSE 'running' END
              WHEN response ? 'error' OR (${unresolvedCanvasCorrection} AND EXISTS(SELECT 1 FROM executions e WHERE e.task_id=tool_calls.task_id AND e.terminal_receipt IS NOT NULL)) THEN 'failed' ELSE 'completed' END AS status,
              response->>'error' AS error_code,response AS result,tool,created_at AS at
            FROM tool_calls WHERE task_id=$1 AND owner_id=$2
          ) operations ORDER BY at`,
          [id, owner],
        )
      ).rows;
    });
  }
  async events(owner: string, id: string, after = 0) {
    await this.get(owner, id);
    return (
      await this.pool.query(
        "SELECT * FROM task_events WHERE owner_id=$1 AND task_id=$2 AND sequence>$3 ORDER BY sequence LIMIT 500",
        [owner, id, after],
      )
    ).rows;
  }
  async audit(owner: string, id: string, after = "0") {
    if (
      !/^(0|[1-9][0-9]{0,18})$/.test(after) ||
      BigInt(after) > 9223372036854775807n
    )
      throw new DomainError("invalid_cursor", 400);
    const task = await this.get(owner, id);
    // A root includes its specialist history; a child exposes only itself.
    // Return only safe state evidence, not requests or execution capabilities.
    const items = (
      await this.pool.query(
        `SELECT a.sequence,a.task_id,a.operation_id,a.event,a.previous_status,a.status,
       a.reason,a.generation,a.receipt_id,a.recovery_id,a.created_at
       FROM execution_audit a JOIN tasks t ON t.id=a.task_id AND t.owner_id=a.owner_id
       WHERE a.owner_id=$1 AND (t.id=$2 OR ($3::boolean AND t.root_id=$2))
       AND a.sequence>$4::bigint ORDER BY a.sequence LIMIT 200`,
        [owner, id, !task.parent_id, after],
      )
    ).rows;
    return { items, nextCursor: items.at(-1)?.sequence ?? after };
  }
}
