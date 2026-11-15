// A failed tool call must not sink an otherwise complete research task by
// itself. Only tools whose output is required for the task to be meaningful
// are fatal, and only when no later call to the same tool succeeded.

export const criticalTools = new Set([
  "market_history",
  "quant_calculate",
  "submit_specialist",
  "get_specialist_result",
  "read_holdings",
  "publish_forecast",
  "publish_forecast_trial_arm",
  "record_review",
  "save_research_summary",
  "canvas_upsert",
  "canvas_patch",
  "canvas_remove",
]);

export type ToolOperation = {
  tool: string;
  status: string;
  error_code?: string | null;
  at: string | Date;
};

export type FatalToolFailure = { tool: string; errorCode: string | null };

/**
 * First failed critical tool that was never recovered by a later successful
 * call to the same tool. `cancelled` is excluded: it is a consequence of the
 * task already stopping, not a cause.
 */
export function fatalToolFailure(
  operations: ToolOperation[],
): FatalToolFailure | null {
  for (const operation of operations) {
    if (operation.status !== "failed" || !criticalTools.has(operation.tool))
      continue;
    const failedAt = new Date(operation.at).getTime();
    const recovered = operations.some(
      (later) =>
        later.tool === operation.tool &&
        later.status === "completed" &&
        new Date(later.at).getTime() >= failedAt,
    );
    if (!recovered)
      return { tool: operation.tool, errorCode: operation.error_code ?? null };
  }
  return null;
}
