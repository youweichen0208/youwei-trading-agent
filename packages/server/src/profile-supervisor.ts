import type { Pool, PoolClient } from "pg";
import { profileTransaction, withProfileControl } from "./profile-control.js";
import type { Role } from "./tasks.js";

export interface ManagedProfile {
  ownerId: string;
  profileId: string;
  role: Role;
  deploymentHash: string;
}
export interface ContainerState {
  id: string;
  status: "running" | "stopped" | "other";
  startedAt?: string;
}
export interface ProfileDriver {
  inspect(profile: ManagedProfile): Promise<ContainerState | undefined>;
  start(profile: ManagedProfile, existingId?: string): Promise<void>;
  stop(profile: ManagedProfile, containerId: string): Promise<void>;
  healthy(profile: ManagedProfile): Promise<boolean>;
}

/** Runs only in the host supervisor; Web/Worker/Hermes need no Docker socket. */
export class ProfileSupervisor {
  constructor(
    readonly pool: Pool,
    private driver: ProfileDriver,
    private idleMs = 300_000,
  ) {
    if (!Number.isFinite(idleMs) || idleMs < 1000)
      throw new Error("invalid_idle_timeout");
  }
  async tick(profile: ManagedProfile) {
    return withProfileControl(
      this.pool,
      profile.profileId,
      false,
      async (c) => {
        try {
          const state = await profileTransaction(
            c,
            profile.ownerId,
            async () => {
              const row = (
                await c.query(
                  `SELECT * FROM profile_instances WHERE id=$1 AND owner_id=$2 AND role=$3`,
                  [profile.profileId, profile.ownerId, profile.role],
                )
              ).rows[0];
              if (
                !row ||
                (row.deployment_hash &&
                  row.deployment_hash !== profile.deploymentHash)
              )
                throw new Error("profile_deployment_mismatch");
              await c.query(
                `UPDATE profile_instances SET lifecycle_managed=true,deployment_hash=$2 WHERE id=$1`,
                [profile.profileId, profile.deploymentHash],
              );
              return row;
            },
          );
          // A failed/indeterminate control action requires explicit operator review.
          // Never retry an uncertain stop concurrently with a fresh start.
          if (
            state.lifecycle_error === "run_reconciliation_pending" ||
            state.lifecycle_error === "container_control_unconfirmed" ||
            state.lifecycle_error === "container_identity_changed"
          )
            return;
          const observed = await this.driver.inspect(profile);
          if (state.container_id && observed?.id !== state.container_id) {
            await this.set(c, profile, "unknown", "container_identity_changed");
            return;
          }
          if (state.state === "stopping") {
            // A previous supervisor may have died after sending stop. Seeing a
            // live container is not proof that its pending stop was cancelled.
            await this.set(
              c,
              profile,
              observed?.status === "stopped" ? "stopped" : "unknown",
              observed?.status === "stopped"
                ? null
                : "container_control_unconfirmed",
            );
            return;
          }
          const action = await profileTransaction(
            c,
            profile.ownerId,
            async () => {
              const demand = (
                await c.query(
                  `SELECT
          count(*) FILTER(WHERE t.status NOT IN ('completed','failed','cancelled','interrupted'))::int AS pending,
          count(*) FILTER(WHERE t.status NOT IN ('completed','failed','cancelled','interrupted')
            AND e.submission_started_at IS NOT NULL)::int AS submitted
          FROM tasks t LEFT JOIN executions e ON e.task_id=t.id
          JOIN thread_contexts tc ON tc.thread_id=t.thread_id AND tc.owner_id=t.owner_id
          JOIN profile_contexts pc ON pc.context_id=tc.context_id AND pc.owner_id=tc.owner_id
          WHERE t.owner_id=$1 AND t.role=$2 AND pc.profile_id=$3
          AND (NOT $4::boolean OR t.status<>'queued')`,
                  [
                    profile.ownerId,
                    profile.role,
                    profile.profileId,
                    process.env.RESEARCH_SINGLE_ROOT === "1",
                  ],
                )
              ).rows[0];
              if (observed)
                await c.query(
                  "UPDATE profile_instances SET container_id=$2,container_started_at=$3 WHERE id=$1",
                  [profile.profileId, observed.id, observed.startedAt ?? null],
                );
              if (demand.pending) {
                await c.query(
                  "UPDATE profile_instances SET last_used_at=now() WHERE id=$1",
                  [profile.profileId],
                );
                if (observed?.status === "running") return "health";
                if (demand.submitted) {
                  await this.set(
                    c,
                    profile,
                    "unknown",
                    "runtime_exited_with_active_execution",
                  );
                  return "none";
                }
                if (!observed || observed.status === "stopped") {
                  if (process.env.RESEARCH_SINGLE_ROOT === "1") {
                    await c.query(
                      "SELECT pg_advisory_xact_lock(hashtext('research-root-capacity'))",
                    );
                    if (
                      (await c.query("SELECT 1 FROM acceptance_capacity"))
                        .rowCount
                    )
                      return "none";
                    const occupied = (
                      await c.query(
                        "SELECT count(*)::int AS n FROM profile_instances WHERE id<>$1 AND lifecycle_managed AND state IN ('starting','ready','stopping','unknown')",
                        [profile.profileId],
                      )
                    ).rows[0].n;
                    if (occupied >= 3) return "none";
                  }
                  await this.set(c, profile, "starting", null);
                  return "start";
                }
                await this.set(
                  c,
                  profile,
                  "unknown",
                  "container_state_unconfirmed",
                );
                return "none";
              }
              if (!observed || observed.status === "stopped") {
                await this.set(c, profile, "stopped", null);
                return "none";
              }
              if (observed.status !== "running") {
                await this.set(
                  c,
                  profile,
                  "unknown",
                  "container_state_unconfirmed",
                );
                return "none";
              }
              if (
                Date.now() - new Date(state.last_used_at).getTime() >=
                this.idleMs
              ) {
                // This owner-locked transition closes beginSubmission's gate before
                // the Docker call. A newly queued task must wait until stop completes.
                await this.set(c, profile, "stopping", null);
                return "stop";
              }
              return "health";
            },
          );
          if (action === "start" || action === "stop") {
            try {
              if (action === "start")
                await this.driver.start(profile, observed?.id);
              else await this.driver.stop(profile, observed!.id);
            } catch {
              await this.set(
                c,
                profile,
                "unknown",
                "container_control_unconfirmed",
              );
              return;
            }
            const after = await this.driver.inspect(profile);
            if (!after || (observed && after.id !== observed.id)) {
              await this.set(
                c,
                profile,
                "unknown",
                "container_identity_changed",
              );
              return;
            }
            await c.query(
              "UPDATE profile_instances SET container_id=$2,container_started_at=$3 WHERE id=$1",
              [profile.profileId, after.id, after.startedAt ?? null],
            );
            if (action === "stop") {
              await this.set(
                c,
                profile,
                after.status === "stopped" ? "stopped" : "unknown",
                after.status === "stopped"
                  ? null
                  : "container_control_unconfirmed",
              );
              return;
            }
          }
          if (action === "start" || action === "health") {
            const healthy = await this.driver
              .healthy(profile)
              .catch(() => false);
            await this.set(
              c,
              profile,
              healthy ? "ready" : "starting",
              healthy ? null : "runtime_not_ready",
            );
          }
        } catch {
          // Inspection/DB failures never authorize creation, replacement or stop.
          // A previous ready flag must not survive a failed identity inspection.
          await c
            .query(
              `WITH locked AS MATERIALIZED (SELECT lock_task_owner($2::uuid) AS found)
         UPDATE profile_instances SET state='unknown',
        lifecycle_error=CASE WHEN lifecycle_error IN ('container_control_unconfirmed','container_identity_changed','run_reconciliation_pending')
          THEN lifecycle_error ELSE 'supervisor_check_failed' END FROM locked WHERE locked.found AND id=$1 AND owner_id=$2`,
              [profile.profileId, profile.ownerId],
            )
            .catch(() => undefined);
        }
      },
    );
  }

  private async reconciliationState(c: PoolClient, profile: ManagedProfile) {
    const row = (
      await c.query(
        `SELECT state,lifecycle_managed,deployment_hash,container_id,container_started_at,lifecycle_error
       FROM profile_instances WHERE id=$1 AND owner_id=$2 AND role=$3`,
        [profile.profileId, profile.ownerId, profile.role],
      )
    ).rows[0];
    if (
      !row ||
      !row.lifecycle_managed ||
      row.deployment_hash !== profile.deploymentHash
    )
      throw new Error("profile_deployment_mismatch");
    const counts = (
      await c.query(
        `SELECT count(*) FILTER(WHERE t.status NOT IN ('completed','failed','cancelled','interrupted'))::int AS pending,
        count(*) FILTER(WHERE t.status NOT IN ('completed','failed','cancelled','interrupted')
          AND e.submission_started_at IS NOT NULL)::int AS submitted,
        (SELECT count(*)::int FROM tool_operations o JOIN tasks ot ON ot.id=o.task_id
          WHERE ot.owner_id=$1 AND ot.role=$2 AND o.status IN ('running','stopping','unknown')) AS tools,
        (SELECT count(*)::int FROM tool_calls tc JOIN tasks ct ON ct.id=tc.task_id
          WHERE ct.owner_id=$1 AND ct.role=$2 AND tc.response IS NULL) AS calls
       FROM tasks t LEFT JOIN executions e ON e.task_id=t.id WHERE t.owner_id=$1 AND t.role=$2`,
        [profile.ownerId, profile.role],
      )
    ).rows[0];
    return { ...row, ...counts };
  }

  /** Read-only operator evidence. This is a snapshot, never authorization to resume. */
  async inspectRecovery(profile: ManagedProfile) {
    return withProfileControl(this.pool, profile.profileId, true, async (c) => {
      const observed = await this.driver.inspect(profile);
      const state = await profileTransaction(c, profile.ownerId, () =>
        this.reconciliationState(c, profile),
      );
      const audit = (
        await c.query(
          `SELECT sequence,previous_state,state,previous_error,error,previous_container_id,container_id,previous_container_started_at,container_started_at,database_actor,created_at
         FROM profile_lifecycle_audit WHERE owner_id=$1 AND profile_id=$2 ORDER BY sequence DESC LIMIT 10`,
          [profile.ownerId, profile.profileId],
        )
      ).rows;
      const recoveries = (
        await c.query(
          `SELECT id,task_id,phase,run_id,admission_id,native_generation,updated_at
         FROM runtime_recoveries WHERE owner_id=$1 AND profile_id=$2 ORDER BY created_at DESC LIMIT 10`,
          [profile.ownerId, profile.profileId],
        )
      ).rows;
      return {
        profileId: profile.profileId,
        ownerId: profile.ownerId,
        role: profile.role,
        ...state,
        observed: observed ?? null,
        audit,
        recoveries,
      };
    });
  }

  /** Clear only a verified stopped original container; never resolve a lost run/tool. */
  async reconcileStopped(profile: ManagedProfile, expectedContainerId: string) {
    if (!/^[a-f0-9]{64}$/.test(expectedContainerId))
      throw new Error("invalid_container_identity");
    return withProfileControl(this.pool, profile.profileId, true, async (c) => {
      const check = (
        state: Awaited<ReturnType<ProfileSupervisor["reconciliationState"]>>,
      ) => {
        if (state.lifecycle_error === "run_reconciliation_pending")
          throw new Error("run_reconciliation_pending");
        if (state.container_id !== expectedContainerId)
          throw new Error("container_identity_changed");
        if (!["unknown", "stopping", "stopped"].includes(state.state))
          throw new Error("profile_not_reconcilable");
        if (state.submitted || state.tools || state.calls)
          throw new Error("profile_execution_unresolved");
      };
      await profileTransaction(c, profile.ownerId, async () =>
        check(await this.reconciliationState(c, profile)),
      );
      // No DB transaction/owner lock is held while waiting for Docker. The shared
      // session lock excludes all supervisor starts/stops for this Profile.
      const observed = await this.driver.inspect(profile);
      if (observed?.id !== expectedContainerId)
        throw new Error("container_identity_changed");
      if (observed.status !== "stopped")
        throw new Error("container_exit_unconfirmed");
      return profileTransaction(c, profile.ownerId, async () => {
        check(await this.reconciliationState(c, profile));
        await this.set(c, profile, "stopped", null);
        const audit = (
          await c.query(
            `SELECT sequence FROM profile_lifecycle_audit WHERE owner_id=$1 AND profile_id=$2 ORDER BY sequence DESC LIMIT 1`,
            [profile.ownerId, profile.profileId],
          )
        ).rows[0];
        return {
          profileId: profile.profileId,
          containerId: expectedContainerId,
          state: "stopped",
          auditSequence: audit?.sequence ?? null,
        };
      });
    });
  }
  private async set(
    c: PoolClient,
    p: ManagedProfile,
    state: string,
    error: string | null,
  ) {
    await c.query(
      `WITH locked AS MATERIALIZED (SELECT lock_task_owner($4::uuid) AS found)
       UPDATE profile_instances SET state=$2,lifecycle_error=$3 FROM locked
       WHERE locked.found AND id=$1 AND owner_id=$4`,
      [p.profileId, state, error, p.ownerId],
    );
  }
}
