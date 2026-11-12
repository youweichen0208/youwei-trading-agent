import { randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import type { Pool, PoolClient } from "pg";
import type { Runtime, Run } from "../../hermes-adapter/src/client.js";
import type { ManagedProfile, ProfileDriver } from "./profile-supervisor.js";
import { profileTransaction, withProfileControl } from "./profile-control.js";

type Phase =
  "stopping" | "stopped" | "starting" | "started" | "resolving" | "resolved";
interface Recovery {
  id: string;
  owner_id: string;
  task_id: string;
  profile_id: string;
  runtime_identity: string;
  container_id: string;
  deployment_hash: string;
  run_id: string;
  session_id: string;
  admission_id: string;
  native_generation: number;
  phase: Phase;
  stopped_container_started_at: string | null;
  restarted_container_started_at: string | null;
  native_receipt: Record<string, unknown> | null;
}
const terminal = (status: string) =>
  ["completed", "failed", "cancelled", "interrupted"].includes(status);

/** Explicit host-operator interruption of a known native unknown turn.
 * No automatic caller, new run, task result write, or external-tool settlement.
 */
export class RuntimeRecovery {
  constructor(
    readonly pool: Pool,
    private driver: ProfileDriver,
  ) {}

  private async state(
    c: PoolClient,
    p: ManagedProfile,
    taskId: string,
    runtime: Runtime,
  ) {
    const task = (
      await c.query(
        `SELECT t.status,e.upstream_run_id,e.session_id,e.runtime_identity,
        e.runtime_container_id,e.runtime_container_started_at,e.runtime_deployment_hash,
        p.container_id,p.deployment_hash,p.lifecycle_managed
       FROM tasks t JOIN executions e ON e.task_id=t.id
       JOIN profile_instances p ON p.id::text=e.profile_id AND p.owner_id=t.owner_id
       WHERE t.id=$1 AND t.owner_id=$2 AND t.role=$3 AND p.id=$4`,
        [taskId, p.ownerId, p.role, p.profileId],
      )
    ).rows[0];
    if (
      !task ||
      !task.lifecycle_managed ||
      task.deployment_hash !== p.deploymentHash ||
      task.runtime_deployment_hash !== p.deploymentHash ||
      task.runtime_identity !== runtime.identity ||
      !task.upstream_run_id ||
      !task.runtime_container_started_at ||
      !/^[a-f0-9]{64}$/.test(task.container_id ?? "") ||
      task.runtime_container_id !== task.container_id
    )
      throw new Error("recovery_binding_mismatch");
    const unsafe = (
      await c.query(
        `SELECT
        EXISTS(SELECT 1 FROM tasks t LEFT JOIN executions e ON e.task_id=t.id
          WHERE t.owner_id=$1 AND t.id<>$2 AND t.status NOT IN ('completed','failed','cancelled','interrupted')
          AND (t.parent_id=$2 OR (t.role=$3 AND e.submission_started_at IS NOT NULL))) AS tasks,
        EXISTS(SELECT 1 FROM tool_operations o JOIN tasks t ON t.id=o.task_id
          WHERE t.owner_id=$1 AND (t.role=$3 OR t.parent_id=$2)
          AND o.status IN ('running','stopping','unknown')) AS tools,
        EXISTS(SELECT 1 FROM tool_calls o JOIN tasks t ON t.id=o.task_id
          WHERE t.owner_id=$1 AND (t.role=$3 OR t.parent_id=$2) AND o.response IS NULL) AS calls`,
        [p.ownerId, taskId, p.role],
      )
    ).rows[0];
    if (unsafe.tasks || unsafe.tools || unsafe.calls)
      throw new Error("profile_execution_unresolved");
    return task;
  }
  private sameRun(run: Run, r: Recovery) {
    if (
      run.run_id !== r.run_id ||
      run.session_id !== r.session_id ||
      run.admission_id !== r.admission_id ||
      run.execution_generation !== r.native_generation
    )
      throw new Error("recovery_native_identity_changed");
  }

  async interruptUnknown(p: ManagedProfile, taskId: string, runtime: Runtime) {
    if (!runtime.resolveUnknown) throw new Error("native_recovery_unavailable");
    return withProfileControl(this.pool, p.profileId, true, async (c) => {
      const owned = <T>(fn: () => Promise<T>) =>
        profileTransaction(c, p.ownerId, fn);
      let record = (
        await c.query<Recovery>(
          "SELECT * FROM runtime_recoveries WHERE task_id=$1 AND owner_id=$2",
          [taskId, p.ownerId],
        )
      ).rows[0];
      if (
        record &&
        (record.profile_id !== p.profileId ||
          record.deployment_hash !== p.deploymentHash ||
          record.runtime_identity !== runtime.identity)
      )
        throw new Error("recovery_binding_mismatch");
      if (record?.phase === "resolved") return record;
      const task = await owned(() => this.state(c, p, taskId, runtime));
      if (!record) {
        if (task.status !== "unknown") throw new Error("task_not_unknown");
        const observed = await this.driver.inspect(p);
        if (!observed || observed.id !== task.container_id)
          throw new Error("container_identity_changed");
        if (observed.status !== "running" || !observed.startedAt)
          throw new Error("recovery_runtime_unavailable");
        const native = await runtime.get(task.upstream_run_id, task.session_id);
        if (
          native.status !== "unknown" ||
          !native.admission_id ||
          native.execution_generation === undefined
        )
          throw new Error("native_run_not_unknown");
        record = await owned(async () => {
          const current = await this.state(c, p, taskId, runtime);
          if (
            current.status !== "unknown" ||
            current.upstream_run_id !== native.run_id ||
            current.session_id !== native.session_id
          )
            throw new Error("task_not_unknown");
          const row = (
            await c.query<Recovery>(
              `INSERT INTO runtime_recoveries(id,owner_id,task_id,profile_id,runtime_identity,container_id,deployment_hash,
              run_id,session_id,admission_id,native_generation,phase)
             VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'stopping') RETURNING *`,
              [
                randomUUID(),
                p.ownerId,
                taskId,
                p.profileId,
                runtime.identity,
                current.container_id,
                p.deploymentHash,
                native.run_id,
                native.session_id,
                native.admission_id,
                native.execution_generation,
              ],
            )
          ).rows[0]!;
          await c.query(
            "UPDATE profile_instances SET state='unknown',lifecycle_error='run_reconciliation_pending' WHERE id=$1",
            [p.profileId],
          );
          return row;
        });
      }
      let r: Recovery = record;
      const inspect = async () => {
        const found = await this.driver.inspect(p);
        if (found?.id !== r.container_id)
          throw new Error("container_identity_changed");
        return found;
      };
      const advance = async (
        phase: Phase,
        token?: string,
        receipt?: Record<string, unknown>,
      ) =>
        owned(async () => {
          await this.state(c, p, taskId, runtime);
          r = (
            await c.query<Recovery>(
              `UPDATE runtime_recoveries SET phase=$2,
           stopped_container_started_at=CASE WHEN $2='stopped' THEN $3 ELSE stopped_container_started_at END,
           restarted_container_started_at=CASE WHEN $2='started' THEN $3 ELSE restarted_container_started_at END,
           native_receipt=CASE WHEN $2='resolved' THEN $4::jsonb ELSE native_receipt END
           WHERE id=$1 RETURNING *`,
              [
                r.id,
                phase,
                token ?? null,
                receipt ? JSON.stringify(receipt) : null,
              ],
            )
          ).rows[0]!;
          if (phase === "resolved")
            await c.query(
              `UPDATE profile_instances SET state='ready',lifecycle_error=NULL,container_started_at=$2,last_used_at=now()
           WHERE id=$1`,
              [p.profileId, r.restarted_container_started_at],
            );
        });
      if (r.phase === "stopping") {
        const observed = await inspect();
        if (observed.status === "running")
          await this.driver.stop(p, r.container_id);
        const stopped = await inspect();
        if (stopped.status !== "stopped" || !stopped.startedAt)
          throw new Error("container_exit_unconfirmed");
        await advance("stopped", stopped.startedAt);
      }
      if (r.phase === "stopped") {
        const stopped = await inspect();
        if (
          stopped.status !== "stopped" ||
          stopped.startedAt !== r.stopped_container_started_at
        )
          throw new Error("recovery_runtime_changed");
        await advance("starting");
      }
      if (r.phase === "starting") {
        const observed = await inspect();
        if (observed.status === "stopped")
          await this.driver.start(p, r.container_id);
        const until = Date.now() + 30_000;
        for (;;) {
          const running = await inspect();
          if (
            running.status === "running" &&
            running.startedAt &&
            running.startedAt !== r.stopped_container_started_at &&
            (await this.driver.healthy(p))
          ) {
            await advance("started", running.startedAt);
            break;
          }
          if (Date.now() >= until)
            throw new Error("recovery_runtime_unavailable");
          await delay(250);
        }
      }
      const running = await inspect();
      if (
        running.status !== "running" ||
        running.startedAt !== r.restarted_container_started_at
      )
        throw new Error("recovery_runtime_changed");
      let native = await runtime.get(r.run_id, r.session_id);
      this.sameRun(native, r);
      if (native.status !== "unknown" && !terminal(native.status))
        throw new Error("native_run_not_unknown");
      if (r.phase === "started") await advance("resolving");
      if (native.status === "unknown") {
        await owned(() => this.state(c, p, taskId, runtime));
        await runtime.resolveUnknown!(
          r.run_id,
          r.admission_id,
          r.native_generation,
        );
        native = await runtime.get(r.run_id, r.session_id);
        this.sameRun(native, r);
      }
      if (!terminal(native.status)) throw new Error("native_exit_unconfirmed");
      const final = await inspect();
      if (
        final.status !== "running" ||
        final.startedAt !== r.restarted_container_started_at
      )
        throw new Error("recovery_runtime_changed");
      await advance("resolved", undefined, {
        run_id: native.run_id,
        session_id: native.session_id,
        status: native.status,
        admission_id: native.admission_id,
        execution_generation: native.execution_generation,
      });
      // Worker independently fetches the real terminal output and waits for all
      // evidence. The operator never forges an application terminal receipt.
      return r;
    });
  }
}
