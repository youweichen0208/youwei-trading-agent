import { DomainError, type Task } from "./tasks.js";

class VerificationPending extends Error {}

// Call under the owner lock, before reserving/starting any new side effect.
export function requireVerifiedTask(task: Task) {
  if (
    task.status === "unknown" &&
    !task.cancel_requested_at &&
    !task.failure_code &&
    task.deadline.getTime() > Date.now()
  )
    throw new VerificationPending();
}

// Retry only a transaction explicitly rolled back BEFORE admission. Never wrap
// provider calls, calculations, network requests or executor-exit persistence.
export async function awaitTaskVerification<T>(
  admit: () => Promise<T>,
): Promise<T> {
  const until = Date.now() + 10_000;
  for (;;) {
    try {
      return await admit();
    } catch (error) {
      if (!(error instanceof VerificationPending)) throw error;
      if (Date.now() >= until) throw new DomainError("tool_not_admissible");
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
}
