import {
  awaitTaskVerification,
  requireVerifiedTask,
} from "./task-verification.js";
import {
  createCipheriv,
  createDecipheriv,
  createHash,
  randomBytes,
  randomUUID,
} from "node:crypto";
import type { Pool, PoolClient } from "pg";
import { transaction, lockOwner } from "./db.js";
import { DomainError, type Task } from "./tasks.js";
import type { ToolContext } from "./data-service.js";

export function canonical(value: unknown): string {
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
export const sha256 = (value: string) =>
  createHash("sha256").update(value).digest("hex");
export async function toolTransaction<T>(
  pool: Pool,
  context: ToolContext,
  fn: (c: PoolClient, t: Task) => Promise<T>,
) {
  return transaction(pool, async (c) => {
    await lockOwner(c, context.ownerId);
    const t = (
      await c.query<Task>(
        `SELECT t.* FROM tasks t JOIN executions e ON e.task_id=t.id
      JOIN profile_instances p ON p.id::text=e.profile_id AND p.owner_id=t.owner_id AND p.role=t.role
      JOIN profile_contexts pc ON pc.profile_id=p.id AND pc.owner_id=t.owner_id
      JOIN thread_contexts tc ON tc.thread_id=t.thread_id AND tc.owner_id=t.owner_id AND tc.context_id=pc.context_id
      WHERE t.id=$1 AND t.owner_id=$2 AND e.profile_id=$3 AND e.submission_started_at IS NOT NULL`,
        [context.taskId, context.ownerId, context.profileId],
      )
    ).rows[0];
    if (!t) throw new DomainError("not_found", 404);
    return fn(c, t);
  });
}
export function assertToolWrite(t: Task) {
  if (
    t.status !== "running" ||
    t.cancel_requested_at ||
    t.failure_code ||
    t.deadline.getTime() <= Date.now()
  )
    throw new DomainError("tool_not_admissible");
}
export interface ToolCall {
  id: string;
  tool: string;
  response: unknown | null;
}
export class ToolCalls {
  constructor(readonly pool: Pool) {}
  async reserve(
    ctx: ToolContext,
    key: string,
    tool: string,
    args: unknown,
  ): Promise<ToolCall> {
    if (!/^[\x21-\x7e]{1,200}$/.test(key))
      throw new DomainError("invalid_operation_key", 400);
    const hash = sha256(canonical({ tool, args }));
    return awaitTaskVerification(() =>
      toolTransaction(this.pool, ctx, async (c, t) => {
        const old = (
          await c.query(
            "SELECT * FROM tool_calls WHERE task_id=$1 AND operation_key=$2",
            [t.id, key],
          )
        ).rows[0];
        if (old) {
          if (old.request_hash !== hash)
            throw new DomainError("operation_key_conflict");
          return old;
        }
        requireVerifiedTask(t);
        assertToolWrite(t);
        if (
          (
            await c.query(
              "SELECT 1 FROM tool_operations WHERE task_id=$1 AND status='unknown'",
              [t.id],
            )
          ).rowCount
        )
          throw new DomainError("tool_execution_unknown");
        if (
          (
            await c.query(
              "SELECT 1 FROM tool_calls WHERE task_id=$1 AND response IS NULL AND created_at<now()-interval '45 seconds'",
              [t.id],
            )
          ).rowCount
        )
          throw new DomainError("tool_execution_unknown");
        const count = (
          await c.query(
            "SELECT count(*) FROM tool_calls c JOIN tasks t ON t.id=c.task_id WHERE t.root_id=$1",
            [t.root_id],
          )
        ).rows[0];
        if (Number(count.count) >= 200)
          throw new DomainError("tool_budget_exceeded", 429);
        return (
          await c.query(
            `INSERT INTO tool_calls(id,owner_id,task_id,operation_key,tool,request_hash) VALUES($1,$2,$3,$4,$5,$6) RETURNING *`,
            [randomUUID(), ctx.ownerId, t.id, key, tool, hash],
          )
        ).rows[0];
      }),
    );
  }
  async finish(ctx: ToolContext, id: string, response: unknown) {
    return toolTransaction(this.pool, ctx, async (c) => {
      const row = (
        await c.query(
          "UPDATE tool_calls SET response=coalesce(response,$4::jsonb) WHERE id=$1 AND task_id=$2 AND owner_id=$3 RETURNING response",
          [id, ctx.taskId, ctx.ownerId, JSON.stringify(response)],
        )
      ).rows[0];
      if (!row) throw new DomainError("not_found", 404);
      return row.response;
    });
  }
}

// Tokens are delivered only over the trusted runtime transport, never in prompts or tool arguments.
export class ToolCapabilities {
  private key: Buffer;
  constructor(
    readonly pool: Pool,
    key: string,
  ) {
    if (!/^[a-f0-9]{64}$/.test(key))
      throw new Error(
        "TOOL_CAPABILITY_KEY must be 32 random bytes encoded as hex",
      );
    this.key = Buffer.from(key, "hex");
  }
  async issue(
    owner: string,
    taskId: string,
    worker: string,
    generation: number,
  ) {
    return transaction(this.pool, async (c) => {
      await lockOwner(c, owner);
      const t = (
        await c.query<Task>("SELECT * FROM tasks WHERE id=$1 AND owner_id=$2", [
          taskId,
          owner,
        ])
      ).rows[0];
      if (
        !t ||
        t.lease_owner !== worker ||
        t.generation !== generation ||
        !t.lease_until ||
        t.lease_until.getTime() <= Date.now()
      )
        throw new DomainError("stale_lease");
      const e = (
        await c.query("SELECT * FROM executions WHERE task_id=$1", [taskId])
      ).rows[0];
      if (!e) throw new DomainError("execution_not_prepared");
      if (e.tool_token_ciphertext) {
        const [iv, tag, encrypted] = e.tool_token_ciphertext.split(":");
        const decipher = createDecipheriv(
          "aes-256-gcm",
          this.key,
          Buffer.from(iv, "hex"),
        );
        decipher.setAAD(Buffer.from(taskId));
        decipher.setAuthTag(Buffer.from(tag, "hex"));
        return Buffer.concat([
          decipher.update(Buffer.from(encrypted, "hex")),
          decipher.final(),
        ]).toString("utf8");
      }
      assertToolWrite(t);
      const token = randomBytes(32).toString("hex"),
        iv = randomBytes(12),
        cipher = createCipheriv("aes-256-gcm", this.key, iv);
      cipher.setAAD(Buffer.from(taskId));
      const encrypted = Buffer.concat([
        cipher.update(token, "utf8"),
        cipher.final(),
      ]);
      const ciphertext = [
        iv.toString("hex"),
        cipher.getAuthTag().toString("hex"),
        encrypted.toString("hex"),
      ].join(":");
      await c.query(
        "UPDATE executions SET tool_token_hash=$2,tool_token_ciphertext=$3 WHERE task_id=$1",
        [taskId, sha256(token), ciphertext],
      );
      return token;
    });
  }
  static async authenticate(pool: Pool, token: string): Promise<ToolContext> {
    if (!/^[a-f0-9]{64}$/.test(token))
      throw new DomainError("unauthorized", 401);
    const row = (
      await pool.query(
        `SELECT t.id,t.owner_id,e.profile_id FROM executions e JOIN tasks t ON t.id=e.task_id
      JOIN profile_instances p ON p.id::text=e.profile_id AND p.owner_id=t.owner_id AND p.role=t.role
      JOIN profile_contexts pc ON pc.profile_id=p.id AND pc.owner_id=t.owner_id
      JOIN thread_contexts tc ON tc.thread_id=t.thread_id AND tc.owner_id=t.owner_id AND tc.context_id=pc.context_id
      WHERE e.tool_token_hash=$1 AND e.submission_started_at IS NOT NULL
      AND t.status IN ('running','stopping','unknown')`,
        [sha256(token)],
      )
    ).rows[0];
    if (!row) throw new DomainError("unauthorized", 401);
    return { ownerId: row.owner_id, taskId: row.id, profileId: row.profile_id };
  }
}
