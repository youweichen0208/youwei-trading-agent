import { dataModeSchema, instantSchema, sha256Schema, researchEvidenceSchema, type DataMode } from "@yta/contracts";
import { z } from "zod";
import { ApiError } from "./errors.js";
import { evidenceAvailableBy } from "./research-report.js";

const fields = {
  context_id: z.uuid(), data_mode: dataModeSchema, as_of: instantSchema,
  data_snapshot_hash: sha256Schema, source_id: z.string().min(1).max(1000), available_at: instantSchema,
};
const receiptSchema = z.strictObject({
  ...fields, schema_version: z.literal("research-tool-result-v1"),
  tool_call_id: z.uuid(), tool: z.enum(["market_snapshot", "indicators"]),
});
const summarySchema = z.strictObject(fields);
const contextSchema = summarySchema.extend({ receipts: z.array(receiptSchema).max(100) });
export type ResearchContext = z.infer<typeof contextSchema>;
export function readResearchContext(input: unknown, mode: DataMode, runId: string): ResearchContext {
  const parsed = contextSchema.safeParse(input);
  if (!parsed.success) throw invalid();
  const context = parsed.data;
  if (context.context_id !== runId || context.data_mode !== mode
    || !evidenceAvailableBy(context.available_at, context.as_of)
    || new Set(context.receipts.map(row => row.tool_call_id)).size !== context.receipts.length
    || context.receipts.some(row => row.context_id !== runId || row.data_mode !== mode
      || row.as_of !== context.as_of || row.data_snapshot_hash !== context.data_snapshot_hash
      || row.source_id !== context.source_id || row.available_at !== context.available_at)) throw invalid();
  return context;
}
export function readResearchContextSummary(input: unknown, mode: DataMode, runId: string) {
  const parsed = summarySchema.safeParse(input);
  if (!parsed.success) throw invalid();
  const { receipts: _receipts, ...summary } = readResearchContext({ ...parsed.data, receipts: [] }, mode, runId);
  return summary;
}
export function contextEvidence(context: ResearchContext) {
  return context.receipts.map(row => researchEvidenceSchema.parse({
    tool_call_id: row.tool_call_id, tool: row.tool, source_id: row.source_id,
    data_snapshot_hash: row.data_snapshot_hash, available_at: row.available_at,
  }));
}
function invalid() { return new ApiError("RESEARCH_CONTEXT_INVALID", "Research evidence scope is invalid", 502); }
