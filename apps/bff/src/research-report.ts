import { researchReportSchema, type DataMode, type ResearchEvidence, type ResearchReport } from "@yta/contracts";
import { ApiError } from "./errors.js";

/** Construct only from authenticated Run scope and verified server/tool responses.
 * Never accept this context from model text, browser input or an external document.
 */
export interface ReportContext {
  data_mode: DataMode;
  as_of: string;
  evidence: readonly ResearchEvidence[];
  draft: { draft_id: string; data_snapshot_hash: string; data_mode: DataMode; as_of: string } | null;
  failure_code: "DATA_UNAVAILABLE" | "DATA_INVALID" | "DRAFT_UNAVAILABLE" | null;
}

// Preserve sub-millisecond ordering; Date.parse alone truncates those digits.
export function evidenceAvailableBy(left: string, right: string): boolean {
  const parse = (value: string) => {
    const match = /^(.*T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
    if (!match?.[1] || !match[3]) return null;
    const seconds = Date.parse(match[1] + match[3]);
    return Number.isFinite(seconds) ? { seconds, fraction: match[2] ?? "" } : null;
  };
  const a = parse(left), b = parse(right);
  if (!a || !b) return false;
  if (a.seconds !== b.seconds) return a.seconds < b.seconds;
  const length = Math.max(a.fraction.length, b.fraction.length);
  return a.fraction.padEnd(length, "0") <= b.fraction.padEnd(length, "0");
}

/** Validates provenance, not truth of prose. This never confirms or freezes a draft. */
export function validateResearchReport(input: unknown, context: ReportContext): ResearchReport {
  const parsed = researchReportSchema.safeParse(input);
  const reject = (): never => { throw new ApiError("RESEARCH_REPORT_INVALID", "Research report does not match verified run evidence", 502); };
  if (!parsed.success) return reject();
  const report = parsed.data;
  if (report.data_mode !== context.data_mode || report.as_of !== context.as_of) return reject();
  const trusted = new Map(context.evidence.map(item => [item.tool_call_id, item]));
  if (trusted.size !== context.evidence.length) return reject();
  const seen = new Set<string>();
  for (const item of report.evidence) {
    const source = trusted.get(item.tool_call_id);
    if (!source || seen.has(item.tool_call_id) || item.tool !== source.tool || item.source_id !== source.source_id
      || item.data_snapshot_hash !== source.data_snapshot_hash || item.available_at !== source.available_at
      || !evidenceAvailableBy(item.available_at, context.as_of)) return reject();
    seen.add(item.tool_call_id);
  }
  if (report.status === "DRAFT_READY") {
    const draft = context.draft;
    if (context.failure_code || !draft || report.draft_id !== draft.draft_id
      || draft.data_mode !== report.data_mode || draft.as_of !== report.as_of
      || new Set(report.evidence.map(item => item.tool)).size !== 2
      || report.evidence.some(item => item.data_snapshot_hash !== draft.data_snapshot_hash)) return reject();
  } else if (!context.failure_code || report.reason_code !== context.failure_code) return reject();
  return report;
}
