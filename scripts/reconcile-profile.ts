import { z } from "zod";
import { database } from "../packages/server/src/db.js";
import { RuntimeRecovery } from "../packages/server/src/runtime-recovery.js";
import { ProfileSupervisor } from "../packages/server/src/profile-supervisor.js";
import {
  loadDeployments,
  DockerProfileDriver,
} from "../packages/hermes-adapter/src/lifecycle.js";

const [mode, registry, directory, profileId, containerId, ...extra] =
  process.argv.slice(2);
const pool = database();
try {
  if (
    !["inspect", "confirm-stopped", "interrupt-run"].includes(mode ?? "") ||
    !registry ||
    !directory ||
    !z.uuid().safeParse(profileId).success ||
    extra.length ||
    (mode === "inspect"
      ? containerId !== undefined
      : mode === "interrupt-run"
        ? !z.uuid().safeParse(containerId).success
        : !/^[a-f0-9]{64}$/.test(containerId ?? ""))
  )
    throw new Error("invalid_recovery_arguments");
  const profiles = await loadDeployments(registry, [directory]);
  const profile = profiles.find((p) => p.profileId === profileId);
  if (!profile) throw new Error("unknown_managed_profile");
  const driver = new DockerProfileDriver(profiles);
  const supervisor = new ProfileSupervisor(pool, driver);
  const result =
    mode === "inspect"
      ? await supervisor.inspectRecovery(profile)
      : mode === "interrupt-run"
        ? await new RuntimeRecovery(pool, driver).interruptUnknown(
            profile,
            containerId!,
            profile.runtime,
          )
        : await supervisor.reconcileStopped(profile, containerId!);
  console.info(JSON.stringify(result, null, 2));
} catch (error) {
  // Only fixed application codes are printable; upstream/DB errors may contain secrets.
  const codes = new Set([
    "invalid_recovery_arguments",
    "run_reconciliation_pending",
    "recovery_binding_mismatch",
    "task_not_unknown",
    "native_run_not_unknown",
    "native_recovery_unavailable",
    "recovery_native_identity_changed",
    "recovery_runtime_unavailable",
    "recovery_runtime_changed",
    "native_exit_unconfirmed",
    "unknown_managed_profile",
    "profile_control_busy",
    "profile_deployment_mismatch",
    "container_identity_changed",
    "profile_not_reconcilable",
    "profile_execution_unresolved",
    "container_exit_unconfirmed",
    "container_isolation_mismatch",
    "deployment_changed",
    "profile_home_replaced",
    "docker_operation_unconfirmed",
  ]);
  const code =
    error instanceof Error && codes.has(error.message)
      ? error.message
      : "profile_reconciliation_refused";
  console.error(code);
  if (code === "invalid_recovery_arguments")
    console.error(
      "usage: pnpm hermes:reconcile inspect VERIFIED_REGISTRY PREPARED_DIRECTORY PROFILE_ID\n" +
        "       pnpm hermes:reconcile confirm-stopped VERIFIED_REGISTRY PREPARED_DIRECTORY PROFILE_ID EXPECTED_CONTAINER_ID\n" +
        "       pnpm hermes:reconcile interrupt-run VERIFIED_REGISTRY PREPARED_DIRECTORY PROFILE_ID TASK_ID",
    );
  process.exitCode = 1;
} finally {
  await pool.end();
}
